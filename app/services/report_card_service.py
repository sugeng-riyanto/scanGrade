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

from app.utils.exam_access import exam_class_ids, result_released
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


def _fetch_profiles(supabase, user_ids):
    """``{id: profile}`` for every id named, in ONE read.

    `class_id` is in the select because a class-filtered set decides membership
    from these rows; without it the filter would have to guess.
    """
    ids = [str(i) for i in (user_ids or []) if i]
    if not ids:
        return {}
    try:
        rows = supabase.table("profiles") \
            .select("id, full_name, nisn, nis, school_id, class_id") \
            .in_("id", ids).execute().data or []
    except Exception:
        logger.exception("Report card: %s profile(s) could not be read", len(ids))
        return {}
    return {str(row.get("id")): row for row in rows if row.get("id")}


def _fetch_schools(supabase, school_ids):
    """``{id: school}`` for every id named, in ONE read — the batch counterpart of
    `_fetch_school`, which reads a single letterhead for the single card."""
    ids = [str(i) for i in (school_ids or []) if i]
    if not ids:
        return {}
    try:
        rows = supabase.table("schools") \
            .select("id, name, address, npsn, city, province, logo_url") \
            .in_("id", ids).execute().data or []
    except Exception:
        logger.exception("Report card: school(s) %s could not be read", ids)
        return {}
    return {str(row.get("id")): row for row in rows if row.get("id")}


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


def prepare_submission(raw):
    """One submission row as the document reads it.

    The embedded exam row is popped, not kept: templates read `submission.exam`,
    and a row carrying both invites them to drift. The exam's JSON columns arrive
    as text on rows written by older paths, and every reader of this document
    indexes them — normalising here rather than in each template is what keeps a
    text column from reaching `.get()`. (The answer key is keyed by question index
    as a string.)
    """
    submission = dict(raw)
    submission["exam"] = submission.pop("exams", None) or {}
    submission["answers"] = _as_dict(submission.get("answers"))
    submission["teacher_feedback"] = _as_dict(submission.get("teacher_feedback"))
    submission.setdefault("is_hidden", False)
    exam = submission["exam"]
    for column in ("answer_key", "question_types", "question_weights"):
        exam[column] = _as_dict(exam.get(column))
    exam["pdf_page_urls"] = _as_list(exam.get("pdf_page_urls"))
    return submission


def build_card(submission, student, teacher_name, school):
    """Everything a report card prints, from rows that are already in hand.

    The single card and the class set both come through here, so a number in one
    cannot be a different number in the other — the class document is the single
    sheet repeated, not a second layout that resembles it.
    """
    student = student or {}
    return {
        "submission": submission,
        "exam": submission["exam"],
        "released": result_released(submission),
        # A profile we could not read is not a reason to drop a paper from a
        # stack of papers: the document carries it with no name, and the count it
        # prints is the count it carries.
        "student_name": student.get("full_name") or "",
        "student_nisn": student.get("nisn") or submission["answers"].get("_nisn") or "",
        "student_nis": student.get("nis") or "",
        "teacher_name": teacher_name or "",
        "school": school or {},
    }


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

    submission = prepare_submission(submission)
    exam = submission["exam"]
    student = _fetch_profile(supabase, submission.get("student_id"))

    # The report belongs to the exam's school. A student's profile is only a
    # fallback, for exams created before the school was recorded on them.
    school = _fetch_school(supabase, exam.get("school_id") or student.get("school_id"))

    teacher_name = ""
    if exam.get("teacher_id"):
        teacher_name = _fetch_profile(supabase, exam["teacher_id"]).get("full_name", "")

    return build_card(submission, student, teacher_name, school)


#: Keys a class set is ordered by. A stack handed down a row opens with the paper
#: somebody can be named for, and `name` first means the order never follows the
#: marks — a set sorted by score announces the ranking as it is passed along.
_SORT_KEY = lambda card: (  # noqa: E731 - one expression, named for what it is
    not card["student_name"],
    (card["student_name"] or "").casefold(),
    card["student_nisn"] or "",
    str(card["submission"].get("id") or ""),
)


def load_report_cards(supabase, exam_id, class_id=None):
    """Every paper in one exam as a list of cards, in hand-back order.

    **The cost does not scale with the class.** `load_report_card` reads a paper in
    four round-trips — the submission, the pupil, the school, the teacher — and
    looping it over a class of thirty is 120 round-trips inside one request, on a
    box that serves 500 pupils with three workers. This reads the same rows in
    three: the papers, then every profile they name at once (pupils and the
    teacher in one call), then the letterhead.

    ``class_id`` keeps only the papers whose owner is in that class. A pupil whose
    profile could not be read has no class to match, so a filtered set excludes
    them — the alternative is falling back to "no filter" and printing another
    class's marks into the document about to be handed out.
    """
    try:
        rows = supabase.table("submissions").select(SUBMISSION_FIELDS) \
            .eq("exam_id", exam_id).execute().data or []
    except Exception:
        logger.exception("Report card: papers for exam %s could not be read", exam_id)
        return []

    submissions = [prepare_submission(row) for row in rows]
    if not submissions:
        return []

    # Every person the document names, in one read: each pupil, and the teacher on
    # every card (one id in practice, whatever the papers say).
    wanted = {s.get("student_id") for s in submissions if s.get("student_id")}
    wanted |= {(s["exam"] or {}).get("teacher_id") for s in submissions}
    people = _fetch_profiles(supabase, wanted)

    if class_id:
        wanted_class = str(class_id)
        submissions = [s for s in submissions
                       if str((people.get(s.get("student_id")) or {}).get("class_id") or "")
                       == wanted_class]
        if not submissions:
            return []

    schools = _fetch_schools(supabase, {
        (s["exam"] or {}).get("school_id")
        or (people.get(s.get("student_id")) or {}).get("school_id")
        for s in submissions
    })

    cards = []
    for submission in submissions:
        exam = submission["exam"] or {}
        student = people.get(submission.get("student_id")) or {}
        school = schools.get(str(exam.get("school_id") or student.get("school_id"))) or {}
        teacher = people.get(exam.get("teacher_id")) or {}
        cards.append(build_card(submission, student, teacher.get("full_name"), school))

    cards.sort(key=_SORT_KEY)
    return cards


#: Returned by `select_class` when a page asked for a class the exam is not
#: assigned to. A caller names it rather than emptying the request silently.
CLASS_NOT_ASSIGNED = "class_not_assigned"


def select_class(exam, requested):
    """Turn a requested class into ``(class_id, refusal)``.

    The exam's own `class_ids` is the only thing that may widen a request: an exam
    is assigned to the classes its teacher ticked, so a class outside that list is
    a request this document must refuse rather than answer with an empty set (which
    looks like "nobody sat it") or with the whole sitting (which puts another
    class's marks on the sheet in a teacher's hand).

    ``None`` on both means "every paper in the sitting", which is what a page with
    no filter asked for.
    """
    if not requested or not str(requested).strip():
        return None, None
    wanted = str(requested).strip()
    if wanted in exam_class_ids(exam):
        return wanted, None
    return None, CLASS_NOT_ASSIGNED
