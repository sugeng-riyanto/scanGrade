"""Which anti-cheat violations a paper is charged for — and who decided so.

The penalty has always been one number on `submissions.penalty`, produced by the
ladder's count. That answers *how much*, and never *which ones*: a teacher looking
at a paper that lost twenty points could not tell a pupil who left the screen twice
from one whose browser dropped fullscreen five times, and had no way to forgive the
one without erasing the other. The banding flow could only move the total, so the
number it wrote and the number the ladder would compute were two answers to the same
question, kept in step by arithmetic rather than by construction.

This module is where that question is answered once. A violation is **applied**
unless a decision says otherwise, and the penalty is the ladder summed over the
applied ones — always derived, never stored as a delta. The decision travels with
the sitting, in the same `submissions.answers` object the appeal already lives in
(one container, so the two cannot disagree), and it is the only thing a teacher
changes; the number follows from it.

Three properties worth stating because each is load-bearing:

* **Absence means applied.** A violation with no decision, or a sitting written
  before this module existed, is charged exactly as it always was. That is what
  keeps every historical mark identical through this change — there is no backfill
  because there is nothing to backfill.
* **The decision is binary, on purpose.** The only two statuses are `applied` and
  `ignored`. Partial credit is expressed by ignoring *some* violations, never by a
  second kind of number; a percentage reduction is a delta the ladder cannot
  re-derive, which is how one number becomes two.
* **The ladder is positional among charged events.** Ignoring the second offence
  must not re-level the third down to the second's price: `final_score.ladder_value`
  prices each event by its position in the full charged list, then the total sums
  only the applied ones. So forgiving one charge forgives exactly that charge.

The two surfaces that *show* the working and the surface that *writes* the mark all
call :func:`breakdown_for`; nothing recomputes the penalty on its own.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from app.services import anti_cheat_service, exam_scoring, final_score
from app.utils.helpers import row_or_none
from app.utils.logger import get_logger

logger = get_logger("penalty")

#: Where a sitting's decisions live inside `submissions.answers`. The leading
#: underscore matches `_penalty_appeal` beside it: both are the app's bookkeeping
#: riding in the student's answer object, not part of what the pupil answered.
DECISIONS_KEY = "_violation_decisions"

#: The only two statuses. Named as a tuple so a caller can validate against it
#: rather than spelling the strings, and so a third cannot appear by typo.
STATUSES = ("applied", "ignored")

#: What a violation with no decision is. A named constant rather than a literal,
#: because it is the answer three different readers depend on being the same.
DEFAULT_STATUS = "applied"

#: The anti-cheat and marking columns `recompute` needs from an exam row, in one
#: select so a caller cannot fetch some and forget the rest.
EXAM_COLUMNS = (
    "id, teacher_id, school_id, anti_cheat_enabled, penalty_per_violation,"
    " max_violations, auto_submit_on_max, question_types, answer_key,"
    " question_weights, total_questions, question_scoring"
)


def _answers(submission: Mapping[str, Any] | None) -> dict:
    """A submission's `answers` as an object, whichever way PostgREST sent it."""
    raw = (submission or {}).get("answers")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def decisions(submission: Mapping[str, Any] | None) -> dict[str, dict]:
    """The decisions recorded on a sitting, keyed by violation id.

    Empty for a sitting nobody has reviewed, which reads everywhere as *all
    applied* — the historical behaviour, so an old mark is never moved by a reader
    that simply has not seen a decision.
    """
    raw = _answers(submission).get(DECISIONS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def with_decisions(events: Iterable[Mapping[str, Any]],
                   recorded: Mapping[str, dict]) -> list[dict]:
    """Each event carrying its decision, defaulting to applied.

    The status is normalised here rather than by each reader, so an unknown value
    (a hand-edited row, a status a later release adds) falls back to the one that
    charges rather than silently forgiving — a decision the app does not understand
    must not become a discount.
    """
    out = []
    for event in events:
        event = dict(event)
        # `_as_event` names the type `kind`; `final_score.penalty_breakdown` prices a
        # violation by `violation_type`. One of them has to bridge the two names, and
        # it is here — so no reader below has to know that the log and the ladder
        # spell the same field differently.
        event["violation_type"] = event.get("violation_type") or event.get("kind")
        decision = recorded.get(str(event.get("id"))) or {}
        status = decision.get("status")
        event["status"] = status if status in STATUSES else DEFAULT_STATUS
        event["reason"] = decision.get("reason")
        event["decided_by"] = decision.get("decided_by")
        event["decided_at"] = decision.get("decided_at")
        out.append(event)
    return out


def events(supabase, exam_id: str, user_id: str,
           submission: Mapping[str, Any] | None = None) -> list[dict]:
    """A paper's charged-and-recorded events, each with its decision."""
    return with_decisions(
        anti_cheat_service.events_for_student(supabase, exam_id, user_id),
        decisions(submission),
    )


def breakdown_for(exam: Mapping[str, Any] | None,
                  events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The applied penalty over events, priced by the paper's own ladder.

    `enabled` is the paper's switch: a school that turned anti-cheat off charges
    nothing and the ladder says so, which is the same answer the log, the count and
    the lock give.
    """
    base = float((exam or {}).get("penalty_per_violation", 5) or 5)
    return final_score.penalty_breakdown(
        events, base=base, enabled=anti_cheat_service.enabled(exam))


def applied_penalty(exam: Mapping[str, Any] | None,
                    events: Iterable[Mapping[str, Any]]) -> float:
    """Just the number, for the ladder that only needs a total."""
    return breakdown_for(exam, events)["total"]


def _apply_decision(answers: dict, violation_id: str, status: str,
                    reason: str | None, decided_by: str | None) -> dict:
    """Write one decision into an answers object and return what it replaced.

    `old` is `None` when there was no prior decision, which is what lets the caller
    keep an audit line that says "applied → ignored" versus "no decision → ignored".
    """
    book = answers.get(DECISIONS_KEY)
    if not isinstance(book, dict):
        book = {}
    previous = book.get(str(violation_id))
    book[str(violation_id)] = {
        "status": status,
        "reason": (reason or None),
        "decided_by": decided_by,
        "decided_at": _now_iso(),
        "previous": (previous.get("status") if isinstance(previous, dict) else None),
    }
    answers[DECISIONS_KEY] = book
    return previous if isinstance(previous, dict) else {}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── The writer ────────────────────────────────────────────────────────────────


def _read_exam(supabase, exam_id: str) -> dict:
    """The exam row a recompute needs, or `{}` — never an exception at a caller.

    A recompute that cannot read its paper must leave the stored mark alone rather
    than guess, so an empty row is the failure answer: `enabled({})` is true and the
    base falls back to the default, which reproduces the ladder the paper had.
    """
    try:
        return row_or_none(supabase.table("exams").select(EXAM_COLUMNS)
                           .eq("id", exam_id).maybe_single().execute()) or {}
    except Exception:
        logger.exception("Could not read exam %s to recompute a penalty", exam_id)
        return {}


def inspect(supabase, submission: Mapping[str, Any],
            exam: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The whole picture **without writing**: raw, penalty, final and the rows.

    The reader the teacher's review panel and every table that shows the working
    call, so what a page *shows* is computed by the same code that decides what it
    *stores* — a preview assembled separately is exactly how the two drift.
    """
    exam_id = submission.get("exam_id")
    if exam is None:
        exam = _read_exam(supabase, exam_id)
    charged = [e for e in events(supabase, exam_id, submission.get("student_id"),
                                 submission)
               if e.get("violation_type") in anti_cheat_service.PENALIZED_VIOLATION_TYPES]
    part = breakdown_for(exam, charged)
    # The ladder keeps position, value and decision; the display fields a teacher
    # needs (the moment, the kind's own label, how long the absence lasted) ride on
    # the event itself. Both lists are the same length and order — `breakdown_for`
    # walks the charged events in order — so they are paired here once, rather than
    # by every caller that wants to show a row.
    from app.services.anti_cheat_service import KIND_LABELS
    for row, event in zip(part["rows"], charged):
        kind = event.get("kind") or event.get("violation_type")
        row["kind"] = kind
        row["at"] = event.get("at")
        row["away_seconds"] = event.get("away_seconds")
        row["label"] = KIND_LABELS.get(kind, (kind, kind))
    # The raw is what the mark scheme paid, independent of any charge: grading the
    # paper with the penalty zeroed is the same arithmetic `rescore` uses to grade
    # it, so the two cannot drift.
    raw = _raw_mark(exam, {**submission, "penalty": 0})
    return {
        "submission_id": submission.get("id"),
        "raw": round(raw, 2),
        "penalty": part["total"],
        "final": final_score.breakdown(raw, part["total"])["final"],
        "recorded": part["recorded"],
        "applied": part["applied"],
        "ignored": part["ignored"],
        "rows": part["rows"],
    }


def recompute(supabase, submission_id: str,
              exam: Mapping[str, Any] | None = None,
              submission: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Re-derive one sitting's penalty and final mark from its decisions.

    The one writer. It reads the events, prices them with the ladder over the
    paper's decisions, re-earns the *raw* mark through the same `rescore` that
    graded the paper (with the penalty zeroed, so the raw is the work and not
    whatever was charged last), and stores `penalty`, `violations` and `final_score`.

    Returns the whole picture — raw, penalty, final and the per-violation rows — so
    the caller that just toggled a switch can show the new number and its working
    without a second query. `None` means the sitting could not be read; nothing is
    written in that case, because a recompute must never invent a mark.
    """
    if submission is None:
        try:
            submission = row_or_none(supabase.table("submissions")
                                     .select("id, exam_id, student_id, answers,"
                                             " teacher_feedback, penalty, final_score,"
                                             " violations")
                                     .eq("id", submission_id).maybe_single().execute())
        except Exception:
            logger.exception("Could not read submission %s to recompute", submission_id)
            submission = None
    if not submission:
        return None

    picture = inspect(supabase, submission, exam)
    try:
        (supabase.table("submissions")
         .update({"penalty": picture["penalty"], "violations": picture["recorded"],
                  "final_score": picture["final"]})
         .eq("id", submission_id).execute())
    except Exception:
        logger.exception("Could not write the recomputed penalty for %s", submission_id)
        return None
    return picture


def _raw_mark(exam: Mapping[str, Any], submission: Mapping[str, Any]) -> float:
    """The submission's earned mark with no penalty — one call into the grader."""
    try:
        parts = exam_scoring.exam_parts(exam)
        return float(exam_scoring.rescore(submission, parts=parts)[0])
    except Exception:
        logger.exception("Could not re-earn the raw mark; falling back to stored")
        # Stored final + stored penalty is the best reconstruction when the grader
        # refuses: it is exact whenever the floor did not bite, which is the common
        # case, and it is still the paper's own numbers rather than a guess.
        return float(submission.get("final_score") or 0) + float(submission.get("penalty") or 0)


def set_decision(supabase, submission_id: str, violation_id: str, status: str,
                 reason: str | None = None, decided_by: str | None = None
                 ) -> tuple[dict[str, Any] | None, dict]:
    """Record one decision and recompute, returning `(picture, audit_facts)`.

    The status is validated here rather than at the route so an unknown value is a
    refusal the caller relays, not a row that later reads as applied-and-confusing.
    `audit_facts` carries what changed for the audit log: the previous status (or
    `None`), the new one, and the reason.
    """
    if status not in STATUSES:
        return None, {"error": "status"}
    try:
        submission = row_or_none(supabase.table("submissions")
                                 .select("id, exam_id, student_id, answers")
                                 .eq("id", submission_id).maybe_single().execute())
    except Exception:
        logger.exception("Could not read submission %s to set a decision", submission_id)
        return None, {"error": "unreadable"}
    if not submission:
        return None, {"error": "missing"}

    answers = _answers(submission)
    previous = _apply_decision(answers, violation_id, status, reason, decided_by)
    try:
        (supabase.table("submissions")
         .update({"answers": json.dumps(answers)})
         .eq("id", submission_id).execute())
    except Exception:
        logger.exception("Could not write a decision for %s", submission_id)
        return None, {"error": "write"}

    picture = recompute(supabase, submission_id, submission={**submission, "answers": answers})
    return picture, {
        "violation_id": violation_id,
        "old_status": (previous.get("status") or DEFAULT_STATUS),
        "new_status": status,
        "reason": reason,
    }


def set_all(supabase, submission_id: str, status: str,
            reason: str | None = None, decided_by: str | None = None,
            violation_ids: Iterable[str] | None = None
            ) -> tuple[dict[str, Any] | None, dict]:
    """Record the same decision for every charged violation of one paper.

    The ids come from the paper's own log, so "ignore all" and "apply all" are the
    set the ladder actually charges — a rotation or an unknown kind is never given a
    decision, because it was never on the ladder to forgive. Returns the picture and
    the count changed, which is what the bulk control reports.
    """
    if status not in STATUSES:
        return None, {"error": "status"}
    try:
        submission = row_or_none(supabase.table("submissions")
                                 .select("id, exam_id, student_id, answers")
                                 .eq("id", submission_id).maybe_single().execute())
    except Exception:
        logger.exception("Could not read submission %s for a bulk decision", submission_id)
        return None, {"error": "unreadable"}
    if not submission:
        return None, {"error": "missing"}

    ids = list(violation_ids) if violation_ids is not None else [
        str(event.get("id")) for event in
        anti_cheat_service.events_for_student(supabase, submission.get("exam_id"),
                                              submission.get("student_id"))
        if event.get("id") and event.get("charged")
    ]
    answers = _answers(submission)
    for vid in ids:
        _apply_decision(answers, vid, status, reason, decided_by)
    try:
        (supabase.table("submissions")
         .update({"answers": json.dumps(answers)})
         .eq("id", submission_id).execute())
    except Exception:
        logger.exception("Could not write bulk decisions for %s", submission_id)
        return None, {"error": "write"}

    picture = recompute(supabase, submission_id, submission={**submission, "answers": answers})
    return picture, {"changed": len(ids), "new_status": status}
