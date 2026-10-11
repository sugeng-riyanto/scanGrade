"""What a submission scores, and what a key change would cost — one arithmetic.

Why this module exists
----------------------
`teacher._recalculate_scores` wrote a pupil's mark out inline, and the question the
answer-key page now has to answer *before* it writes anything — which pupils would
move, and by how much — is that same arithmetic applied to a different key. Two
implementations of "what does this submission score" is two answers to the question
a teacher is deciding on, and the one that would be believed is the preview while
the one that would be persisted is the writer. So the loop lives here once:

* :func:`exam_parts` reads an exam's marking inputs exactly as the recalculation has
  always read them — the key, the types, the weights (with the defaults a paper with
  none has always been given), the paper's length, the AKM scoring modes;
* :func:`rescore` is that recalculation for **one** submission, optionally under a key
  and type map that has not been written yet;
* :func:`key_change_impact` calls it twice per pupil and counts who moves.

Nothing here touches the database or a request: the caller loads the rows, which is
what makes the preview testable against the writer instead of against a mock of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from app.services.question_types import (
    complete_weights, default_weights, earned_points, objective_result,
)


@dataclass(frozen=True)
class ExamParts:
    """The four things a mark depends on, resolved once per exam.

    Rebuilt per submission, `default_weights` + `complete_weights` would run on every
    pupil's paper for the same answer — and the two functions are the whole reason a
    paper's shares total 100, so they are resolved once and carried.
    """

    question_types: Mapping[str, Any]
    answer_key: Mapping[str, Any]
    weights: Mapping[str, Any]
    total: int
    scoring: Mapping[str, Any] | None


def _mapping(value: Any) -> Mapping[str, Any]:
    """A jsonb column as an object, whether it arrived parsed or as text.

    PostgREST hands back a `jsonb` column parsed and a text one as a string, and
    which shape arrives depends on the column's type in a given project — the same
    reason `teacher._json_fields` exists. Tolerating both here is what lets the
    recalculation stop mutating the submission dicts it was handed.
    """
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def exam_parts(exam: Mapping[str, Any]) -> ExamParts:
    """This exam's marking inputs, with the defaults the writer has always applied.

    A paper stored without weights falls back to the 70/30 split, and one missing a
    question's share gets its remainder — both rules were inline in the
    recalculation, and a preview that skipped either would report a mark the write
    would not produce.
    """
    question_types = _mapping(exam.get("question_types"))
    answer_key = _mapping(exam.get("answer_key"))
    total = int(exam.get("total_questions") or 0)
    weights = _mapping(exam.get("question_weights"))
    if not weights and total > 0:
        weights = default_weights(question_types, total)
    return ExamParts(
        question_types=question_types,
        answer_key=answer_key,
        weights=complete_weights(weights, total),
        total=total,
        scoring=_mapping(exam.get("question_scoring")) or None,
    )


def after_edit(parts: ExamParts, index: Any, qtype: Any, key: Any) -> tuple[dict, dict]:
    """The type and key maps this exam would have with one question written.

    One helper rather than two dict copies at each call site: the review panel asks
    this once per candidate answer and the answer-key page once per save, and a
    caller that replaced the key but not the type (or the reverse) would be measuring
    an edit nobody proposed.
    """
    types = dict(parts.question_types)
    types[str(index)] = qtype
    answer_key = dict(parts.answer_key)
    answer_key[str(index)] = key
    return types, answer_key


def rescore(sub: Mapping[str, Any], *, parts: ExamParts,
            question_types: Mapping[str, Any] | None = None,
            answer_key: Mapping[str, Any] | None = None) -> tuple[float, float]:
    """(final mark, objective score) this submission earns — the writer's own rule.

    `question_types` and `answer_key` override the exam's own, which is how the same
    rule answers "what *would* this pupil score" for an edit that has not happened.
    The objective score is a percentage of the paper's objective questions with an
    unkeyed question scored wrong; the final mark is the weighted total, capped at
    100, minus the penalty, floored at 0.
    """
    qt = parts.question_types if question_types is None else question_types
    ak = parts.answer_key if answer_key is None else answer_key
    answers = _mapping(sub.get("answers"))
    earned, _graded = earned_points(qt, ak, answers, parts.weights, parts.total,
                                    parts.scoring)
    # A teacher's per-question mark rides on top of the auto-graded total: `scores`
    # holds a percentage of that question's own share, so it is scaled by the share
    # before it is added.
    feedback = _mapping(_mapping(sub.get("teacher_feedback")).get("scores"))
    for index, value in feedback.items():
        if value is None or value == "":
            continue
        weight = float(parts.weights.get(str(index), 0) or 0)
        if weight > 0:
            earned += float(value) / 100.0 * weight
    final = round(min(earned, 100), 2)
    final = max(0, round(final - float(sub.get("penalty") or 0), 2))
    score = objective_result(qt, ak, answers, parts.total, parts.scoring).score
    return final, score


def key_change_impact(subs, *, parts: ExamParts,
                      question_types: Mapping[str, Any] | None = None,
                      answer_key: Mapping[str, Any] | None = None) -> dict:
    """Who moves, and how far, if these types and keys are written.

    "Before" is each submission's **stored** mark — what the pupil's page shows right
    now — and "after" is :func:`rescore` under the edit about to be made, which is
    what the recalculation would persist. Comparing two fresh computations would
    measure a paper both sides had already drifted from; comparing stored against
    stored would measure nothing. Counts and the largest single move, not a list of
    names: the question being decided is "does this cost the class", and names are a
    page the teacher would have to hold in their head.

    `moved` is the number the caller gates on: zero means the edit cannot change
    anybody's mark, so nothing has to be asked.
    """
    up = down = same = 0
    worst = 0.0
    for sub in subs:
        before = float(sub.get("final_score") or 0)
        after, _score = rescore(sub, parts=parts, question_types=question_types,
                                answer_key=answer_key)
        delta = round(after - before, 2)
        if delta > 0:
            up += 1
        elif delta < 0:
            down += 1
        else:
            same += 1
        worst = max(worst, abs(delta))
    return {"up": up, "down": down, "same": same, "total": up + down + same,
            "moved": up + down, "worst": worst}


#: The statuses whose marks a save may move: a submission that was graded, marked by
#: hand or published to the pupil. A draft has no mark to change and a `in_progress`
#: sitting has not been marked yet, so neither is measured — the same three statuses
#: the recalculation has always rewritten.
MARKED_STATUSES = ("submitted", "graded", "published")


def load_parts(supabase, exam_id: str) -> tuple[dict, ExamParts] | None:
    """(exam row, its parts) for an exam, or None when it is gone.

    The one place the marking inputs are read from the database, so the writer and
    both previews cannot select different columns or apply different defaults.
    """
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return None
    return exam, exam_parts(exam)


def load_marked(supabase, exam_id: str) -> list[dict]:
    """Every submission of this exam whose mark a recomputation would rewrite."""
    return supabase.table("submissions").select(
        "id, answers, penalty, teacher_feedback, score, final_score"
    ).eq("exam_id", exam_id).in_("status", list(MARKED_STATUSES)).execute().data or []
