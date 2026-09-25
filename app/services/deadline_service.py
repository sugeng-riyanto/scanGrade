"""The server ends the sitting, so a closed browser cannot keep one open forever.

The only thing that used to end a sitting was the exam page's own countdown:
`if (this.timeLeft <= 0) this.submitExam(true)`. That is a picture of the
deadline, not the deadline. A student whose laptop closed at minute 59, whose
phone died, whose tab the browser discarded, or who simply never pressed Send left
the row in `draft` — and a `draft` is not a submission: it is absent from the
teacher's results list, absent from the analysis, and absent from the exam's own
statistics, permanently. Nobody chose that. The clock ran out somewhere that was
no longer running.

This module is the one enforcement point, and it holds to four rules:

* **The arithmetic is asked for, never re-derived.** ``exam_window.deadline``
  already answers "when does this sitting end" from both clocks — the duration
  counted from the student's own ``started_at``, and the assignment window end
  when the exam is set to stop there. A sweep that recomputes it is a second
  answer to the same question, and the two go out of step the first time a teacher
  edits a window.

* **The row is written by the one writer.** ``submission_service.finish_sitting``
  owns the ``submissions`` row — the unique constraint counts *every* status, so
  choosing between PATCH and POST by hand is exactly what used to answer a student
  with a raw duplicate-key error. The sweep goes through it, so a clock-closed
  paper and a hand-submitted one are written the same way, from the same mark
  scheme and the same penalty ladder.

* **The grace belongs to both halves.** A paper arriving within
  ``exam_window.LATE_GRACE_SECONDS`` of the deadline is *accepted* — that is what
  the grace means — so the sweep must not close that sitting yet, or a student
  whose countdown reaches zero would be racing the box for their own answers.
  "Accepted" and "not yet closed" read the same constant on purpose.

* **A paper the clock closed was not late.** ``submitted_late`` means the answers
  arrived after the deadline. Here they arrived before it and the sitting was
  ended *by* it, so the mark stays false — and ``submitted_at`` is the deadline
  itself rather than the moment the sweep noticed, so the record does not move
  with the tick interval or with how long a box happened to be down.

Nothing that enforces no end is ever closed: an exam with no duration and no
window end has no deadline, and a sitting under it stays open indefinitely, which
is what the teacher asked for.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from app.services.anti_cheat_service import (
    calculate_graduated_penalty, count_penalized_violations,
)
from app.services.question_types import default_weights, earned_points, objective_result
from app.services.submission_service import finish_sitting
from app.utils import exam_window
from app.utils.logger import get_logger

logger = get_logger("deadline")

#: Only a `draft` is a sitting still in progress. A live row is a result, and a
#: result is not the sweep's to rewrite.
DRAFT_STATUS = "draft"

#: How many drafts one pass looks at. Bounds the read on a busy box; anything
#: beyond it is picked up by the next tick, and the deadline arithmetic — not this
#: — decides what actually closes.
SCAN_LIMIT = 500

#: The read filter's only job is to keep the query small. No sitting can be over
#: sooner than this, and the exact answer still comes from `exam_window.deadline`.
MIN_SITTING_SECONDS = 60

#: How often the sweep runs. Short enough that a paper stops being a draft shortly
#: after it is over, long enough to cost three queries a minute on a 1-vCPU box.
SWEEP_INTERVAL_SECONDS = 60

#: The exam columns the closing payload is built from — the same ones the submit
#: route reads, because the mark has to be the same mark.
EXAM_COLUMNS = (
    "id,duration_minutes,start_at,end_at,auto_submit_on_window_end,publish_mode,"
    "total_questions,answer_key,question_types,question_weights"
)

SUBMISSION_COLUMNS = (
    "id,exam_id,student_id,status,started_at,answers,"
    "exams(" + EXAM_COLUMNS + ")"
)


def _now(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


def sitting_end(exam, started_at):
    """The instant this sitting ended, or None when nothing enforces an end."""
    return exam_window.deadline(exam or {}, started_at)


def has_expired(exam, started_at, now=None) -> bool:
    """Is the deadline — plus the grace a paper may still arrive within — behind us?"""
    limit = sitting_end(exam, started_at)
    if limit is None:
        return False
    return _now(now) > limit + timedelta(seconds=exam_window.LATE_GRACE_SECONDS)


def _exam_of(row) -> dict:
    """The exam embedded on a submission row, whichever shape PostgREST used."""
    exam = (row or {}).get("exams")
    if isinstance(exam, list):
        exam = exam[0] if exam else None
    return exam if isinstance(exam, dict) else {}


def _as_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return value or {}


def _candidates(supabase, now):
    """Drafts old enough that a deadline could have passed. One round-trip."""
    cutoff = (now - timedelta(seconds=MIN_SITTING_SECONDS)).isoformat()
    return (
        supabase.table("submissions")
        .select(SUBMISSION_COLUMNS)
        .eq("status", DRAFT_STATUS)
        .lt("started_at", cutoff)
        .order("started_at")
        .limit(SCAN_LIMIT)
        .execute()
        .data
    ) or []


def _closed_payload(supabase, row, exam, ended_at) -> dict:
    """What a clock-closed paper records — built exactly as a submitted one is.

    The same question-type rule, the same weight fallback, the same penalty ladder
    the submit route climbs, so a paper the clock ended and a paper the student
    sent cannot disagree about the mark.
    """
    answers = _as_json(row.get("answers"))
    question_types = _as_json(exam.get("question_types"))
    answer_key = _as_json(exam.get("answer_key"))
    total = int(exam.get("total_questions") or 0)
    weights = _as_json(exam.get("question_weights"))
    if not weights and total > 0:
        weights = default_weights(question_types, total)

    earned, _graded = earned_points(question_types, answer_key, answers, weights, total)
    score = objective_result(question_types, answer_key, answers, total).score
    violations = count_penalized_violations(
        supabase, row.get("student_id"), row.get("exam_id"))
    penalty = calculate_graduated_penalty(violations, exam).get("penalty", 0)

    return {
        "exam_id": row.get("exam_id"),
        "student_id": row.get("student_id"),
        "answers": answers,
        "score": score,
        "max_score": 100.0,
        "violations": violations,
        "penalty": round(penalty, 2),
        "final_score": max(0.0, round(earned - penalty, 2)),
        "status": "submitted",
        "is_published": exam.get("publish_mode") == "auto",
        # The answers were already here when the clock ran out; calling that
        # "late" would be a statement about the box, not about the student.
        "submitted_late": False,
        # The deadline, not the moment this pass noticed — so the record does not
        # depend on the tick interval or on how long a box was down.
        "submitted_at": ended_at.isoformat(),
    }


def close_expired(supabase, now=None) -> dict:
    """Close every sitting whose deadline has passed. Returns what it closed.

    Idempotent: a row is only ever read as a candidate while it is a `draft`, so a
    second pass over the same instant finds nothing to do. A single row that cannot
    be written is logged and skipped rather than stopping the sweep — one bad paper
    must not leave a whole exam open.
    """
    now = _now(now)
    closed = []
    for row in _candidates(supabase, now):
        exam = _exam_of(row)
        if not exam:
            continue
        started = row.get("started_at")
        if not has_expired(exam, started, now):
            continue
        ended_at = sitting_end(exam, started)
        try:
            payload = _closed_payload(supabase, row, exam, ended_at)
            finish_sitting(
                supabase, row.get("exam_id"), row.get("student_id"), payload, rows=[row]
            )
        except Exception:  # noqa: BLE001 — one paper must not stop the sweep
            logger.exception("Could not close the expired sitting %s", row.get("id"))
            continue
        row.update(payload)
        closed.append(row.get("id"))

    if closed:
        logger.info("Deadline sweep closed %d expired sitting(s)", len(closed))
    return {"closed": len(closed), "ids": closed}


# ── the loop ─────────────────────────────────────────────────────────────────

_deadline_thread = None
_deadline_interval = SWEEP_INTERVAL_SECONDS
_running = False


def _run_deadline_loop(app=None):
    """Sweep on a timer.

    `close_expired` needs a Supabase client, which comes from the application
    config, so each pass runs inside an application context when one was handed in
    — the same reason the retention loop takes the app. Without it the sweep would
    fail every tick and the deadline would silently go back to being the browser's
    business.
    """
    global _running
    _running = True
    while _running:
        try:
            from app.utils.auth import get_supabase
            if app is not None:
                with app.app_context():
                    close_expired(get_supabase())
            else:
                close_expired(get_supabase())
        except Exception as e:  # noqa: BLE001 — a failed pass is retried next tick
            logger.error("Deadline sweep error: %s", e)
        time.sleep(_deadline_interval)


def start_deadline_scheduler(interval=SWEEP_INTERVAL_SECONDS, app=None):
    """Start the background sweep (safe to call multiple times)."""
    global _deadline_thread, _deadline_interval
    if _deadline_thread and _deadline_thread.is_alive():
        return
    _deadline_interval = interval or SWEEP_INTERVAL_SECONDS
    _deadline_thread = threading.Thread(
        target=_run_deadline_loop, args=(app,), daemon=True)
    _deadline_thread.start()
    logger.info("Deadline sweep started (interval=%ds)", _deadline_interval)


def stop_deadline_scheduler():
    global _running
    _running = False
