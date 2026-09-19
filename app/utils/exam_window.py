"""The two clocks an exam runs on — and the one place both are read.

A sitting is bounded by two different things, and they are not interchangeable:

* the **window** — ``start_at`` … ``end_at`` — says when a student may *begin*.
  ``end_at`` is the teacher's "assignment window end": after it nobody new starts.
* the **duration** — ``duration_minutes`` counted from the student's own
  ``started_at`` — says how long that student has once begun. The teacher form says
  so in as many words: the duration "may extend beyond the assignment window end
  date depending on when the student begins".

``auto_submit_on_window_end`` swaps the second clock for the first: the sitting
ends at ``end_at`` whatever time the student began, so a late starter does not get
the whole duration. The deadline is then the **earlier** of the two, which is what
"the student has until the date and time defined in the Assignment Window End field
regardless of when they start" means once a duration is also set.

The four callers that need an answer — the exam list, the door into an exam, the
sync API and the submit route — all ask here. Answering separately is how a page
ends up telling a student "12 minutes left" while the server treats the paper as
finished, and it is the same drift `exam_access.py` was written to stop for the
RBAC questions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: `window_state` answers. BEFORE and CLOSED are different failures with different
#: sentences: "not yet" is something a student waits out, "too late" is not.
OPEN = "open"
BEFORE = "before"
CLOSED = "closed"

#: Which clock decided a sitting's deadline. The exam page says which one, so a
#: student can tell "the paper is timed" from "everyone stops when the window shuts".
DEADLINE_DURATION = "duration"
DEADLINE_WINDOW_END = "window_end"

#: How late a submission may arrive before it is recorded as late rather than
#: refused. A phone that loses its network at the last second must not lose the
#: answers, but a paper that arrives twenty minutes after the deadline is not a
#: timed sitting any more and the teacher should be able to see that.
LATE_GRACE_SECONDS = 120


def parse_dt(value):
    """A timestamp as Supabase returns it, or None.

    A ``timestamptz`` column arrives as an ISO string through PostgREST and as a
    ``datetime`` when a row was built in Python. Both are read the same way here so
    that no caller has to care which one it got, and a bare (naive) value is read as
    UTC because that is what the column holds.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def to_utc_iso(value: str | None, tz_offset_hours: int = 7) -> str | None:
    """A ``datetime-local`` form value, as UTC ISO.

    The form has no timezone: a teacher in Jakarta types 09:00 and means 09:00
    there. ``start_at`` was already converted this way; the window end is converted
    by the same function so the two cannot end up on different clocks, which is the
    one mistake here that silently moves a deadline by seven hours.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        local = datetime.fromisoformat(text)
    except ValueError:
        return text
    return (local - timedelta(hours=tz_offset_hours)).isoformat()


def window_state(exam, now=None) -> str:
    """OPEN, BEFORE or CLOSED for *beginning* this exam, at ``now``.

    No window at all is OPEN: an exam a teacher published without dates is one
    students may sit, and that is what every existing row does.
    """
    now = now or datetime.now(timezone.utc)
    start, end = parse_dt(exam.get("start_at")), parse_dt(exam.get("end_at"))
    if start and now < start:
        return BEFORE
    if end and now > end:
        return CLOSED
    return OPEN


def may_begin(exam, now=None) -> tuple[bool, str]:
    """``(allowed, reason)`` — the window governs beginning and nothing else."""
    state = window_state(exam, now)
    if state == BEFORE:
        return False, "before_start"
    if state == CLOSED:
        return False, "window_closed"
    return True, OPEN


def deadline(exam, started_at, now=None):
    """When this sitting must end, or None when nothing enforces an end.

    ``duration_minutes`` of 0 is the form's "Tak terbatas / Unlimited": there is no
    duration, so in window-end mode the window end is still the end, and otherwise
    the paper simply has no deadline.
    """
    started = parse_dt(started_at)
    minutes = exam.get("duration_minutes") or 0
    end = parse_dt(exam.get("end_at"))
    by_duration = started + timedelta(minutes=minutes) if (started and minutes > 0) else None
    if exam.get("auto_submit_on_window_end") and end:
        return end if by_duration is None else min(by_duration, end)
    return by_duration


def deadline_reason(exam, started_at) -> str:
    """Which clock decided the deadline — DURATION unless the window cut it short."""
    limit = deadline(exam, started_at)
    end = parse_dt(exam.get("end_at"))
    if limit is not None and end is not None and limit == end:
        return DEADLINE_WINDOW_END
    return DEADLINE_DURATION


def seconds_left(exam, started_at, now=None) -> int | None:
    """Seconds until the sitting ends; None when nothing enforces an end."""
    limit = deadline(exam, started_at)
    if limit is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((limit - now).total_seconds()))


def is_late(exam, started_at, submitted_at, now=None) -> bool:
    """Did this submission beat the deadline (allowing the grace)?"""
    limit = deadline(exam, started_at)
    arrived = parse_dt(submitted_at) or now or datetime.now(timezone.utc)
    if limit is None:
        return False
    return arrived > limit + timedelta(seconds=LATE_GRACE_SECONDS)


def page_facts(exam, started_at=None, now=None) -> dict:
    """Everything a student page needs about the clocks, computed once.

    Handed to the template as one object so that the countdown, the reason it is
    counting down, and the moment it runs out cannot come from three different
    readings of the same row.
    """
    now = now or datetime.now(timezone.utc)
    limit = deadline(exam, started_at, now)
    left = None if limit is None else max(0, int((limit - now).total_seconds()))
    return {
        "state": window_state(exam, now),
        "deadline_iso": limit.isoformat() if limit else None,
        "seconds_left": left,
        "reason": deadline_reason(exam, started_at),
        "window_end_iso": (parse_dt(exam.get("end_at")) or None).isoformat()
        if parse_dt(exam.get("end_at")) else None,
    }
