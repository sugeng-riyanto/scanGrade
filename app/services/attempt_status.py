"""One answer to "what is the state of this sitting", for every surface.

Before this module, each screen answered the question its own way: the exam page
counted toward a deadline it was handed, the sync API recomputed `server_time_left`
from the stored `started_at`, the deadline sweep decided `has_expired`, and the lock
gate asked `resume_code.at_or_past_deadline`. They agree today because they all call
`exam_window`, but the *status vocabulary* — active, locked, finished, expired — was
re-derived in each place, which is one edit away from a teacher's dashboard calling a
paper "finished" while its pupil is still writing.

So this is the one function. `get_attempt_status` reads the row once and returns the
whole answer — status, the absolute deadline, seconds left, and the server's clock —
and every surface that needs one of those numbers asks here rather than recomputing
it. `heartbeat` records liveness through the same function, and `record_event` is the
audit half: one row per transition, never one row per ping.

The clock is never derived here either. `exam_window.deadline` is the only arithmetic;
this module only *reads* it, which is why the deadline cannot drift between the page,
the sweep and the lock gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.utils import exam_window
from app.utils.helpers import row_or_none
from app.utils.logger import get_logger
from app.services.submission_service import LIVE_STATUSES
from app.services.submission_service import LOCKED_STATUS as _LOCKED_STATUS

logger = get_logger("attempt_status")

#: The statuses this module returns. Deliberately a small, closed set so a caller
#: cannot invent a sixth.
ACTIVE = "active"
LOCKED = "locked_pending_resume"
FINISHED = "finished"
EXPIRED = "expired"
MISSING = "missing"

#: Which statuses are still a *sitting* — the paper a pupil may still be on. The
#: page, the heartbeat and the sweep all read this one tuple.
ONGOING = (ACTIVE, LOCKED)

#: The two statuses a *sitting* can hold — the paper a pupil is still on. `DRAFT` is
#: the ordinary case; `locked_pending_resume` is still a sitting, not a result. The
#: deadline sweep's candidate set and the lock gate's transitions both read this, so
#: there is one spelling of "the statuses a sitting can be".
DRAFT = "draft"
LOCKED_STATUS = _LOCKED_STATUS
OPEN_STATUSES = (DRAFT, LOCKED_STATUS)

#: The grace a *submission* may still arrive within. Re-exported so a surface asking
#: "is this sitting over" reads the one constant rather than importing the arithmetic
#: module and applying it itself.
GRACE_SECONDS = exam_window.LATE_GRACE_SECONDS

#: How long a gap between pings reads as "the connection went away" rather than
#: "the page was between ticks". Three missed pings at the page's 25s cadence.
GAP_SECONDS = 90

#: The message a pupil sees per status. Keys, not sentences: the language lives in
#: the browser, and every page here translates a key rather than a server string.
MESSAGE_KEYS = {
    ACTIVE: "attempt_active",
    LOCKED: "attempt_locked",
    FINISHED: "attempt_finished",
    EXPIRED: "attempt_expired",
    MISSING: "attempt_missing",
}

#: The columns the deadline needs, and the liveness columns. One read.
EXAM_COLUMNS = (
    "id,duration_minutes,start_at,end_at,auto_submit_on_window_end,"
    "lock_pending_resume,resume_code_limit,publish_mode,school_id"
)
ROW_COLUMNS = (
    "id,exam_id,student_id,status,started_at,answers,"
    "resume_limit,resume_count_used,locked_at,last_resumed_at,"
    "last_ping_at,ping_count"
)


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


def _rows(supabase, exam_id, student_id) -> list[dict]:
    """This pupil's row for this exam, read with the columns the status needs.

    A local read rather than `submission_service.sitting_rows`, which selects the
    narrower set the writer needs — the status has to see the liveness and resume
    columns, and asking for them in one query is what keeps this a single read.
    """
    return (
        supabase.table("submissions").select(ROW_COLUMNS)
        .eq("exam_id", exam_id).eq("student_id", student_id)
        .execute().data
        or []
    )


def deadline_of(exam, row, started_at=None):
    """When this sitting ends, or ``None`` when nothing enforces an end.

    The one arithmetic, exposed: a caller that needs the instant (to stamp a closed
    paper with the deadline rather than the moment a sweep noticed) asks here rather
    than reaching for `exam_window` and holding a second answer.
    """
    started = started_at if started_at is not None else (row or {}).get("started_at")
    return exam_window.deadline(exam or {}, started)


def at_or_past_deadline(exam, row, now=None) -> bool:
    """Has this sitting reached its end — with no grace applied?

    The lock gate's question. The grace lets a *submission* arrive late; a grace on
    the resume door would let a pupil answer past a deadline the server has already
    treated as final.
    """
    limit = deadline_of(exam, row)
    if limit is None:
        return False                      # nothing enforces an end: open indefinitely
    return _now(now) >= limit


def expired(exam, row, now=None) -> bool:
    """Is the deadline — plus the grace a paper may still arrive within — behind us?

    The sweep's question: a paper arriving inside the grace is *accepted*, so the
    sitting must not be closed yet or a pupil whose countdown hits zero would be
    racing the box for their own answers.
    """
    limit = deadline_of(exam, row)
    if limit is None:
        return False
    return _now(now) > limit + timedelta(seconds=GRACE_SECONDS)


def _status_of(row, exam, now):
    """The one place the four statuses are decided from a row and the clock."""
    state = (row or {}).get("status")
    if state in LIVE_STATUSES:
        return FINISHED
    if state == LOCKED_STATUS:
        # A locked sitting whose clock has run out is *not* still resumable: it is
        # a paper that is over, waiting only to be written closed. Saying "locked"
        # here would offer a pupil a resume that cannot be granted — and the lock
        # gate asks this same function, so the two answers cannot differ.
        if at_or_past_deadline(exam, row, now):
            return EXPIRED
        return LOCKED
    if state == DRAFT:
        if expired(exam, row, now):
            return EXPIRED
        return ACTIVE
    # retracted (a voided attempt, reopenable) and anything unknown read as
    # "nothing to resume": the page offers the exam again rather than a state it
    # cannot honour.
    return MISSING


def get_attempt_status(supabase, exam_id, student_id, *, exam=None, row=None,
                       now=None, include_answers=False) -> dict:
    """The single source of truth for one (student, exam) sitting.

    Reads the submission row (and the exam, unless handed one) and returns a
    consistent answer::

        {
          "ok": bool,               # a row exists
          "status": active|locked_pending_resume|finished|expired|missing,
          "attempt_id", "exam_id",
          "server_now": iso, "deadline": iso|None,
          "seconds_left": int|None, "deadline_reason": "duration"|"window_end",
          "started_at", "last_ping_at", "resume_used", "resume_limit",
          "message_key", "answers" (only when asked),
        }

    Idempotent by construction: it writes nothing. A call for a non-existent row is
    a `missing` answer, not an error and not a created attempt.
    """
    now = _now(now)
    rows = [row] if row is not None else _rows(supabase, exam_id, student_id)
    row = rows[0] if rows else None

    if exam is None:
        try:
            exam_row = row_or_none(
                supabase.table("exams").select(EXAM_COLUMNS)
                .eq("id", exam_id).maybe_single().execute()
            )
            exam = _exam_of_row(exam_row)
        except Exception:  # noqa: BLE001 — an unreadable exam is not an attempt
            exam = {}

    if row is None:
        return _payload(MISSING, exam_id, None, exam, None, now)

    status = _status_of(row, exam, now)
    return _payload(status, exam_id, row, exam, None, now,
                    include_answers=include_answers)


def _exam_of_row(res) -> dict:
    data = res if isinstance(res, (dict, list)) else getattr(res, "data", None)
    data = data or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    return data if isinstance(data, dict) else {}


def _payload(status, exam_id, row, exam, extra, now, *, include_answers=False) -> dict:
    started = (row or {}).get("started_at")
    limit = exam_window.deadline(exam or {}, started) if row else None
    left = None if limit is None else max(0, int((limit - now).total_seconds()))
    payload = {
        "ok": row is not None,
        "status": status,
        "exam_id": exam_id,
        "attempt_id": (row or {}).get("id"),
        "server_now": now.isoformat(),
        "deadline": limit.isoformat() if limit else None,
        "seconds_left": left,
        "deadline_reason": exam_window.deadline_reason(exam or {}, started) if row else None,
        "started_at": started,
        "last_ping_at": (row or {}).get("last_ping_at"),
        "resume_used": (row or {}).get("resume_count_used"),
        "resume_limit": (row or {}).get("resume_limit"),
        "message_key": MESSAGE_KEYS.get(status, MESSAGE_KEYS[MISSING]),
    }
    if extra:
        payload.update(extra)
    if include_answers and status in ONGOING:
        payload["answers"] = (row or {}).get("answers") or {}
    return payload


def heartbeat(supabase, exam_id, student_id, *, exam=None, now=None) -> dict:
    """Record one liveness ping — observation only, never a lock.

    Updates `last_ping_at`/`ping_count` in a single write and answers with the same
    status payload a caller would get from `get_attempt_status`, so the page can use
    the ping to re-sync its clock without a second round trip. When the gap since
    the previous ping exceeds `GAP_SECONDS`, the transition is written as events
    (`connection_gap`, `connection_resumed`) — once per return, not per ping.

    **It cannot lock a sitting.** Nothing here writes `status`, and a missing or
    late ping is not a violation: a pupil whose phone lost signal must not be
    ejected by the mechanism that exists to notice the loss.
    """
    now = _now(now)
    rows = _rows(supabase, exam_id, student_id)
    row = rows[0] if rows else None
    status = get_attempt_status(supabase, exam_id, student_id, exam=exam, row=row, now=now)
    if row is None or status["status"] not in ONGOING:
        return status

    previous = exam_window.parse_dt(row.get("last_ping_at"))
    try:
        count = int(row.get("ping_count") or 0)
    except (TypeError, ValueError):
        count = 0
    stamp = now.isoformat()
    try:
        supabase.table("submissions").update({
            "last_ping_at": stamp, "ping_count": count + 1,
        }).eq("id", row["id"]).execute()
    except Exception:  # noqa: BLE001 — a failed ping is not a verdict on the pupil
        logger.warning("heartbeat write failed for attempt %s", row.get("id"))

    if previous is not None:
        gap = (now - previous).total_seconds()
        if gap > GAP_SECONDS:
            record_event(
                supabase, row["id"], "connection_gap", meta={
                    "last_ping_at": previous.isoformat(), "gap_seconds": int(gap),
                }, now=now,
            )
            record_event(supabase, row["id"], "connection_resumed", now=now)
    status["last_ping_at"] = stamp
    return status


# ── the audit trail: one row per transition, never one per ping ─────────────

#: The kinds this module writes. `violation_logs` keeps the charged absences;
#: these are the connection and lifecycle transitions beside them.
EVENT_KINDS = (
    "attempt_started", "connection_gap", "connection_resumed",
    "attempt_locked", "attempt_resumed", "attempt_finalized",
)


def record_event(supabase, attempt_id, kind, *, meta=None, offline=False,
                 duration_ms=None, question_index=None, occurred_at=None,
                 now=None) -> bool:
    """Write one transition into `attempt_session_events`. Best-effort.

    The table numbers events per attempt with `UNIQUE (attempt_id, seq)`, so the
    sequence is read as `max(seq) + 1` — one small read, paid only on a transition
    (a gap, a lock, a resume), never on the 25-second ping. A write that fails is
    logged and swallowed: the audit trail must never cost a pupil their paper, the
    same rule `submission_service` follows for its summaries.
    """
    if not attempt_id:
        return False
    when = _now(now)
    try:
        latest = (
            supabase.table("attempt_session_events").select("seq")
            .eq("attempt_id", attempt_id).order("seq", desc=True).limit(1).execute().data
            or []
        )
        seq = int(latest[0].get("seq") or 0) + 1 if latest else 1
        if occurred_at is None:
            occurred_iso = when.isoformat()
        elif isinstance(occurred_at, datetime):
            occurred_iso = occurred_at.isoformat()
        else:
            occurred_iso = occurred_at
        supabase.table("attempt_session_events").insert({
            "attempt_id": attempt_id,
            "seq": seq,
            "kind": kind,
            "occurred_at": occurred_iso,
            "server_at": when.isoformat(),
            "duration_ms": duration_ms,
            "offline": bool(offline),
            "question_index": question_index,
            "meta": meta or {},
        }).execute()
        return True
    except Exception:  # noqa: BLE001 — derived evidence, never a pupil's paper
        logger.warning("could not record the %s event for attempt %s", kind, attempt_id)
        return False


def timeline(supabase, attempt_id) -> list[dict]:
    """Every transition this attempt recorded, oldest first.

    A read that fails is an empty list and a log line, never an exception thrown
    at a page: a dashboard that 500s because its evidence is missing is worse than
    one that says it has no evidence (the same rule `anti_cheat_service` follows).
    """
    try:
        rows = (
            supabase.table("attempt_session_events")
            .select("seq,kind,occurred_at,server_at,duration_ms,offline,question_index,meta")
            .eq("attempt_id", attempt_id).order("seq").execute().data
            or []
        )
        return rows
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the session timeline for attempt %s", attempt_id)
        return []
