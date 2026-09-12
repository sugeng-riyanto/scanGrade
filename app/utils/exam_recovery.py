"""Recovery codes — how a student gets back into a session from another device.

A six-digit code is issued when an exam session starts and shown to the student in
the exam topbar. It is a *way back*, never a permission: redeeming one runs the same
``exam_sitting_allowed`` check as opening the exam, so a code can never open an exam
the student could not otherwise open.

What it buys, and why the page exists at all: ``/student/exams`` is served with
``Cache-Control: max-age=30`` and its query swallows failures and returns an empty
list — so a student on a borrowed phone can be looking at a stale or empty list while
their session is still open server-side. The code resolves the exam directly by id
and is bound to the student, so it does not depend on the list at all.
"""
import logging
import random
from datetime import datetime, timezone

from app.utils.helpers import row_or_none

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
CODE_MIN = 10 ** (CODE_DIGITS - 1)
CODE_MAX = 10 ** CODE_DIGITS - 1


def _random_code() -> str:
    return str(random.randint(CODE_MIN, CODE_MAX))


def issue_code(supabase, student_id, exam_id):
    """Return this student's code for this exam, creating it at most once.

    Idempotent on purpose. Re-issuing on every page load would invalidate the
    number the student already wrote down — precisely when a flaky connection
    makes them reload. Returns ``None`` when no code can be stored; opening an
    exam must never depend on this feature working.
    """
    try:
        existing = row_or_none(
            supabase.table("exam_access_codes")
            .select("code")
            .eq("student_id", student_id).eq("exam_id", exam_id)
            .order("created_at", desc=True).limit(1).maybe_single().execute()
        )
        if existing and existing.get("code"):
            return existing["code"]

        # Redemption always filters on student_id, so a clash only matters
        # between this student's own codes.
        taken = {
            (row or {}).get("code")
            for row in (
                supabase.table("exam_access_codes").select("code")
                .eq("student_id", student_id).execute().data or []
            )
        }
        for _ in range(20):
            code = _random_code()
            if code not in taken:
                break
        else:
            logger.warning("No free recovery code for student %s", student_id)
            return None

        supabase.table("exam_access_codes").insert({
            "exam_id": exam_id,
            "student_id": student_id,
            "code": code,
        }).execute()
        return code
    except Exception:
        logger.exception("Could not issue a recovery code for exam %s", exam_id)
        return None


def redeem_code(supabase, student_id, code):
    """Return the exam id this student's code points at, or ``None``.

    Scoped to ``student_id``: another student's rows are never searched, so a guess
    learns nothing about anyone else's session and no shared secret has to be kept
    between students.

    First redemption is stamped into ``is_used``/``used_at`` for the audit trail.
    It does not lock the code out — a student whose second device also dies mid-exam
    must still be able to get back, and the code only ever leads to an exam this
    same student is already allowed to sit.
    """
    code = (code or "").strip()
    if len(code) != CODE_DIGITS or not code.isdigit():
        return None

    try:
        row = row_or_none(
            supabase.table("exam_access_codes")
            .select("id,exam_id")
            .eq("student_id", student_id).eq("code", code)
            .order("created_at", desc=True).limit(1).maybe_single().execute()
        )
    except Exception:
        logger.exception("Recovery code lookup failed")
        return None

    if not row or not row.get("exam_id"):
        return None

    try:
        supabase.table("exam_access_codes").update({
            "is_used": True,
            "used_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", row["id"]).execute()
    except Exception:
        logger.exception("Could not stamp recovery code %s as used", row["id"])

    return row["exam_id"]
