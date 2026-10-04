"""Locking a sitting out, and letting its own pupil back in — without moving the clock.

There is **one code**, and it already existed
------------------------------------------------
This module deliberately mints nothing. `app/utils/exam_recovery.py` has issued a
six-digit code per (student, exam) since before this feature: created idempotently
when the session starts, shown to the student in the exam topbar from the first
minute, scoped to that student, and redeemable so a dead phone or a stale exam list
is not the end of a sitting.

The first version of this feature introduced a *second* code — `submissions.resume_code`
— shown on the same screen. Two codes on one exam page is a question the student
cannot answer ("which one do I type?"), and two systems that both mean "a way back
into this paper" is exactly the parallel-subsystem shape this repository keeps
closing. So the lock borrows the code that is already there: `unlock` below asks
`exam_recovery.matches`, and the *new* part is only the lock gate around it —
`locked_pending_resume`, the usage allowance, and the deadline rule.

What the lock gate owns, and the code does not
----------------------------------------------
The recovery code says *"this is your session"*. It says nothing about whether the
session may be reopened — that is this module, and it is four checks in an order that
matters (see `unlock`). A code correct for this student still cannot reopen a paper
that is over, or one whose allowance is spent.

The clock is never an input here
--------------------------------
`attempt_status` owns both answers — "when does this end" (`deadline_of`) and "has it
ended" (`at_or_past_deadline`) — and it reads `started_at` plus the exam's own duration
(and the window end, when the exam is set to stop there). So this module *never derives
a deadline*, and every write below deliberately omits `started_at`. The guards in
`tests/unit/test_resume_code.py` assert the exact payload of each transition, because
"the deadline did not move" is only believable if the column it is derived from is
absent from every write.

Locking is not a pause: the clock keeps running while a pupil waits, exactly as it
would have if they had stared at the wall, so a pupil locked for three minutes loses
those three minutes and is not compensated. That is the school's decision and the
honest one — a lock that bought time would reward leaving the paper's fullscreen,
which is the act it exists to discourage.

At or past the deadline, a lock is refused
------------------------------------------
Applied at both doors with one helper, so they cannot disagree by a second. Locking a
sitting whose deadline has passed would hold a pupil out of a paper that is over,
waiting for a resume that can never be granted.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services import attempt_status
from app.utils.logger import get_logger

logger = get_logger("resume_code")

#: What a sitting may be reopened with when neither the exam nor the row says.
DEFAULT_LIMIT = 2

#: The two statuses this gate moves between, named from the one definition rather
#: than spelled here: `attempt_status` owns the vocabulary, and a second literal is
#: a second answer to "what is this sitting".
DRAFT = attempt_status.DRAFT
LOCKED = attempt_status.LOCKED_STATUS

#: What `decide` answers, and what a caller acts on.
NONE = "none"
LOCK = "lock"
FINALIZE = "finalize"
RESUMED = "resumed"

#: Refusal keys, translated by the pages. Fixed strings rather than sentences:
#: the language lives in the browser, which the server never sees.
WRONG_CODE = "wrong_code"
NOT_LOCKED = "not_locked"
DEADLINE_PASSED = "deadline_passed"
LIMIT_REACHED = "limit_reached"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().isoformat()


def _write(supabase, row: dict, payload: dict) -> list[dict]:
    """One update on this row by id, or nothing when the row has no id."""
    row_id = (row or {}).get("id")
    if not row_id or not payload:
        return []
    try:
        return (supabase.table("submissions").update(payload)
                .eq("id", row_id).execute().data or [])
    except Exception as exc:  # noqa: BLE001 — a refused write is a refusal, not a 500
        logger.warning("resume_code: write refused: %s", exc)
        return []


# ── the deadline, asked for and never re-derived ────────────────────────────

def at_or_past_deadline(exam: dict, row: dict, now: datetime | None = None) -> bool:
    """Has this sitting reached its end?

    Asked of `attempt_status.at_or_past_deadline` — the one place that answers it —
    so locking, resuming and the deadline sweep cannot disagree about when the paper
    is over. No grace is applied there either: the grace lets a *submission* arrive
    late, and a grace on the resume door would let a pupil answer past a deadline the
    server has already treated as final.
    """
    return attempt_status.at_or_past_deadline(exam or {}, row or {}, now=now)


# ── the threshold, which is the exam's own ──────────────────────────────────

def threshold(exam: dict) -> int:
    """The charged-violation count that triggers the lock: the exam's `max_violations`.

    A second threshold would be a second answer to one question, and the ladder
    already counts against this one.
    """
    try:
        return int((exam or {}).get("max_violations") or 0)
    except (TypeError, ValueError):
        return 0


def locking_enabled(exam: dict) -> bool:
    """Whether this exam locks rather than simply ending at the threshold.

    Two conditions, both required: the school asked for it (`lock_pending_resume`),
    and violations are being counted at all (`anti_cheat_enabled`). With anti-cheat
    off nothing is charged, so there is nothing to cross.
    """
    if not (exam or {}).get("lock_pending_resume"):
        return False
    return (exam or {}).get("anti_cheat_enabled") is not False


def effective_limit(exam: dict, row: dict) -> int:
    """How many times this sitting may be reopened, the sitting's answer first.

    The row's snapshot wins because it was copied when the attempt began: a school
    that lowers the policy mid-exam must not retroactively shorten the allowance of
    a pupil already sitting.
    """
    for value in ((row or {}).get("resume_limit"), (exam or {}).get("resume_code_limit")):
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return DEFAULT_LIMIT


def decide(exam: dict, row: dict, violations: int, now: datetime | None = None) -> str:
    """What to do about a charged-violation count: nothing, lock, or finalise.

    The order is the whole rule:

    * not opted in, or nothing is counted, or the threshold is "unlimited" → NONE,
      which leaves today's ladder (its own auto-submit) in charge;
    * below the threshold → NONE;
    * at or past the deadline → FINALIZE **before** the lock branch, so a lock is
      never written for a paper that is already over;
    * otherwise → LOCK.
    """
    if not locking_enabled(exam):
        return NONE
    limit = threshold(exam)
    if limit <= 0:
        return NONE                      # `max_violations = 0` is the form's unlimited
    try:
        charged = int(violations or 0)
    except (TypeError, ValueError):
        charged = 0
    if charged < limit:
        return NONE
    if at_or_past_deadline(exam, row, now):
        return FINALIZE
    if (row or {}).get("status") != DRAFT:
        return NONE                      # already locked, or already a result
    return LOCK


# ── locking ─────────────────────────────────────────────────────────────────

def lock(supabase, row: dict, exam: dict, *, violations: int = 0,
         now: datetime | None = None) -> dict:
    """Lock this sitting pending the pupil's own recovery code — or refuse, if late.

    The payload carries no `started_at`, no `submitted_at` and no `end_at`: every one
    of those is an input to the deadline, and the clock keeps running.
    """
    action = decide(exam, row, violations, now)
    if action != LOCK:
        return {"ok": False, "action": action, "reason": ""}
    payload = {
        "status": LOCKED,
        "locked_at": (now or _now()).isoformat(),
        "updated_at": _stamp(),
    }
    # Snapshot the allowance the first time it matters, if the sitting carries none.
    if (row or {}).get("resume_limit") is None:
        payload["resume_limit"] = effective_limit(exam, row)
    written = _write(supabase, row, payload)
    if not written:
        return {"ok": False, "action": LOCK, "reason": "write_failed"}
    row.update(payload)
    _record(supabase, row, "attempt_locked", {
        "violations": violations, "resume_limit": payload.get("resume_limit"),
    })
    return {"ok": True, "action": LOCK, "reason": "", "payload": payload}


# ── reopening ───────────────────────────────────────────────────────────────

def unlock(supabase, row: dict, exam: dict, *, student_id: str, code: str,
           now: datetime | None = None) -> dict:
    """Let the pupil back in with their own session code, before their own deadline.

    Checked in this order, and the order is the rule:

    1. **is it actually locked** — reopening a paper that is not locked is a request
       from a stale page, not a recovery;
    2. **the deadline** — asked before anything else that costs a query, because when
       it has passed the honest answer is that the exam is over, and no code changes
       that. The paper is finalised on what was saved;
    3. **the allowance** — spent, so a teacher is the way back (the manual route);
    4. **the code** — the existing recovery code, via `exam_recovery.matches`, which
       is scoped to this student and this exam. It is asked last because it is the
       only check that reads another table.

    A successful resume writes the status, the count and the stamp, and **nothing
    else** — in particular not `locked_at`, not `started_at`, and not anything the
    deadline is derived from.
    """
    if (row or {}).get("status") != LOCKED:
        return {"ok": False, "action": NONE, "reason": NOT_LOCKED}
    if at_or_past_deadline(exam, row, now):
        return {"ok": False, "action": FINALIZE, "reason": DEADLINE_PASSED}

    try:
        used = max(0, int((row or {}).get("resume_count_used") or 0))
    except (TypeError, ValueError):
        used = 0
    if used >= effective_limit(exam, row):
        return {"ok": False, "action": NONE, "reason": LIMIT_REACHED}

    from app.utils import exam_recovery
    if not exam_recovery.matches(supabase, student_id,
                                 (row or {}).get("exam_id"), code):
        return {"ok": False, "action": NONE, "reason": WRONG_CODE}

    payload = {
        "status": DRAFT,
        "resume_count_used": used + 1,
        "last_resumed_at": (now or _now()).isoformat(),
        "updated_at": _stamp(),
    }
    written = _write(supabase, row, payload)
    if not written:
        return {"ok": False, "action": NONE, "reason": "write_failed"}
    row.update(payload)
    _record(supabase, row, "attempt_resumed", {
        "resume_count_used": payload.get("resume_count_used"),
    })
    return {"ok": True, "action": RESUMED, "reason": "", "payload": payload}


def _record(supabase, row, kind, meta=None):
    """One lifecycle event for this attempt. Lazily imported, best-effort."""
    try:
        from app.services import attempt_status
        attempt_status.record_event(supabase, (row or {}).get("id"), kind, meta=meta)
    except Exception:  # noqa: BLE001
        logger.warning("resume_code: could not record %s for %s", kind, (row or {}).get("id"))
