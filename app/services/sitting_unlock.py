"""The staff manual unlock — one implementation for every door that holds it.

`resume_code.manual_unlock` owns the two rules that belong to the clock (not
locked → not a recovery; at or past the deadline → finalise, not unlock, and write
nothing). What it deliberately does *not* own is **who** may ask: that is the
caller's, because it differs by role — a teacher is bounded to the exams they hold,
while a vice-principal or the school admin answers for the whole school.

This module is the part that must not differ between those callers: the exam has to
be the school's, the sitting has to exist, the gate decides, a paper whose clock
ended is finalised rather than reopened, and the actor is written to the activity
log. Duplicating that in two routes is how the two doors would drift apart — one
would finalise and the other would refuse to, and nobody would notice until a pupil
was stranded.

Authority stays with the caller: the teacher's route checks the held set before it
calls this, and the official routes rely on their own school scoping plus the
school check here. This function answers with a **reason key** the pages already
translate (`shared/_invigilation_reasons.html`), never a sentence.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from app.services import attempt_status as status_service
from app.services import deadline_service
from app.services import resume_code as rc
from app.services.audit_service import log_activity


def unlock_sitting(supabase, school_id, exam_id, student_id, actor_id) -> dict:
    """Reopen a locked sitting in this school, or finalise it.

    Returns ``{"ok": bool, "reason": str, "action": "finalize" | "unlocked" | ...}``
    where ``reason`` is a key from the shared reasons partial. The clock is never
    moved: a paper the clock already ended is finalised with the answers saved up to
    the lock, exactly as the code path would.
    """
    exam = status_service.exam_row(supabase, exam_id)
    # The exam has to be this school's. The caller's own scoping already narrows
    # it, but naming the reason here means neither door trusts the other's reads.
    if not exam or str(exam.get("school_id") or "") != str(school_id or ""):
        return {"ok": False, "reason": "exam_not_in_school"}

    row = status_service.sitting_row(supabase, exam_id, student_id)
    if row is None:
        return {"ok": False, "reason": rc.NOT_LOCKED}

    out = rc.manual_unlock(supabase, row, exam)
    if out.get("action") == rc.FINALIZE:
        try:
            deadline_service.finalize_expired(supabase, row, exam)
        except Exception:  # noqa: BLE001 — the refusal stands even if closing fails
            logger.exception(
                "Could not finalise the locked sitting %s for exam %s",
                student_id, exam_id)
        log_activity("finalize", "submission", row.get("id"),
                     new_data={"exam_id": exam_id, "manual": True},
                     user_id=actor_id)
        return {"ok": False, "action": "finalize", "reason": "submission_finalized"}

    if not out.get("ok"):
        return {"ok": False, "reason": out.get("reason") or "write_failed"}

    log_activity("unlock", "submission", row.get("id"),
                 new_data={"exam_id": exam_id, "manual": True},
                 user_id=actor_id)
    return {"ok": True, "action": "unlocked", "reason": "unlock_ok"}
