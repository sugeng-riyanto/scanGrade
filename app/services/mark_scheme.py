"""What each question is worth, and how a paper reaches exactly 100.

The app has marked papers with two percentages since the start: an objective pool
and an essay pool, each split **equally** among the questions in it. That model
cannot express a mark scheme a teacher actually writes down — "one mark each for the
twenty choice questions, three for each matching question, twenty for the essay" —
because every objective question gets the same marks whether it is a one-line
true/false or a four-pair matching item. Two other things fell out of it:

* a pool holds a *percentage*, so three essay questions behind 70% are 23.33 points
  each and the paper totals **99.99**;
* every objective question was all-or-nothing, so a four-pair matching question
  answered three ways right earned **nothing**.

So the scheme is per *type*, and the paper is fixed up to exactly 100:

    {mcq: 1, true_false: 1, match: 3, drag_drop: 3, order: 3, essay: 10}

Every type in that map is a kind of question in its own right, with its own row and
its own marks. Multiple choice is not the parent of the others: a true/false, a
matching, a drag & drop and an ordering question each stand on their own, which is
what makes the table readable and what the "./shared weight" model this replaces got
wrong.

`build_weights` turns that into points per question and scales them to exactly 100.0.
The scaling is real and it is shown: a paper whose raw marks already total 100 is
left alone, and one whose marks total 96 has every question scaled by 100/96 — because
a score reported out of 100 has to be out of 100, and 99.99 is what the old model
produced. Rounding cannot reintroduce it: `normalise_to_100` works in whole steps of
`MARK_STEP` and hands the leftover steps to the largest remainders, so the parts add
up to 100 exactly rather than approximately.

The scheme is stored inside `question_weights` under `_scheme` (see
`question_types.SCHEME_KEY`), and that is what makes the guarantee a teacher needs
possible: **an exam saved before schemes existed carries no `_scheme`, awards no
part-marks, and scores exactly what it scored yesterday.** Scoring here is
retroactive by construction — a result is recomputed from the stored answers at
publish time and whenever marks are recalculated — so a scheme has to be something a
paper *has*, never something a release applies to every paper at once.

This module is the authority on the numbers. The builder carries a JavaScript copy so
the total moves as a teacher types, and `tests/unit/test_mark_scheme.py` runs both
over the same inputs and fails when they disagree; the save route recomputes the
points here rather than trusting what the page posted, so the copy can only ever be a
preview.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from app.services.question_types import (
    DEFAULT_TYPE,
    DRAG_DROP,
    ESSAY,
    ESSAY_CANVAS,
    ESSAY_TEXT,
    MATCH,
    MCQ,
    ORDER,
    SCHEME_KEY,
    TRUE_FALSE,
    canonical_type,
    kind_label,
    scheme_in,
)

#: The paper's own total. A scheme's question marks are scaled to land here.
TOTAL = 100.0

#: The granularity a mark may have. A tenth, because that is already the precision
#: this app reports (`MCQ: 12.5` on the grading page), and because a paper with seven
#: equal questions cannot be divided into whole marks.
MARK_STEP = 0.1

#: What a question of each type is worth before scaling — the scheme a teacher gets
#: when they ask for one and have not said otherwise. Objective questions are worth
#: integers because that is how they are priced on paper; an essay is worth ten
#: because it is marked against a rubric rather than answered right or wrong.
DEFAULT_MARKS: dict[str, float] = {
    MCQ: 1.0,
    TRUE_FALSE: 1.0,
    MATCH: 3.0,
    DRAG_DROP: 3.0,
    ORDER: 3.0,
    ESSAY_CANVAS: 10.0,
    ESSAY_TEXT: 10.0,
    ESSAY: 10.0,
}

#: The order the scheme is shown in: the types a teacher writes on a paper, in the
#: order they are usually written. **One row per type**, and that is the point of
#: the scheme: multiple choice, true/false, matching, drag & drop, ordering and the
#: essay each carry their own marks and none of them is a flavour of another — a
#: matching question worth three marks beside a choice question worth one is a mark
#: scheme, and a single "objective weight" split between them is not.
#:
#: Ordering sits beside drag & drop rather than inside it because the two ask for
#: the same thing in different words — both answer a sequence — and a teacher choosing
#: between "drag these into the box" and "rank these" is choosing the question's
#: presentation, which is theirs to choose.
SCHEME_ORDER: tuple[str, ...] = (
    MCQ, TRUE_FALSE, MATCH, DRAG_DROP, ORDER, ESSAY_CANVAS,
)


def default_marks(raw_type: Any) -> float:
    """What this type is worth unless the teacher says otherwise."""
    return float(DEFAULT_MARKS.get(canonical_type(raw_type), DEFAULT_MARKS[MCQ]))


def marks_for(raw_type: Any, by_type: Mapping[str, Any] | None = None) -> float:
    """This type's marks in a scheme, falling back to the default for its type.

    A scheme that names only some types is normal rather than broken: a teacher who
    sets the matching questions to 4 marks has not thereby decided anything about
    the essays. Naming a type the app does not know is not an error either — the
    value is simply never asked for, because no question carries that type.
    """
    kind = canonical_type(raw_type)
    by_type = by_type or {}
    value = by_type.get(kind)
    if value is None:
        return default_marks(kind)
    try:
        marks = float(value)
    except (TypeError, ValueError):
        return default_marks(kind)
    return marks if marks >= 0 else 0.0


def normalise_to_100(points: list[float], step: float = MARK_STEP) -> list[float]:
    """The same proportions, re-based so they sum to exactly 100.

    Largest-remainder: every value is floored to a whole step, then the steps left
    over go to the values that lost the most in the flooring. Rounding each value
    independently — the obvious version — is what makes a total of 99.9 or 100.1, and
    a mark scheme whose parts do not add up to its own total is the defect this
    replaces.
    """
    if not points:
        return []
    if step <= 0:
        raise ValueError("step must be positive")

    values = [max(0.0, float(p)) for p in points]
    total = sum(values)
    if total <= 0:
        # Every question worth nothing: equal shares is the only answer that keeps
        # the paper summing to 100 without inventing a preference.
        values = [1.0] * len(values)
        total = float(len(values))

    target_units = int(round(TOTAL / step))
    exact = [value * TOTAL / total for value in values]
    units = [int(math.floor(value / step + 1e-9)) for value in exact]
    remaining = target_units - sum(units)

    if remaining > 0:
        order = sorted(
            range(len(values)),
            key=lambda i: (-(exact[i] / step - units[i]), i),
        )
        for n in range(remaining):
            units[order[n % len(order)]] += 1
    elif remaining < 0:
        # Reachable only through floating-point noise in `exact`; take the steps
        # back from the smallest remainders so the total is still exact.
        order = sorted(
            range(len(values)),
            key=lambda i: (exact[i] / step - units[i], i),
        )
        for n in range(-remaining):
            pick = order[n % len(order)]
            if units[pick] > 0:
                units[pick] -= 1

    return [round(unit * step, 4) for unit in units]


def type_counts(
    question_types: Mapping[str, Any] | None, total_questions: int
) -> dict[str, int]:
    """How many questions of each type this paper has, in scheme order."""
    types = question_types or {}
    counts: dict[str, int] = {t: 0 for t in SCHEME_ORDER}
    for i in range(total_questions or 0):
        kind = canonical_type(types.get(str(i), DEFAULT_TYPE))
        if kind in counts:
            counts[kind] += 1
        else:
            # A legacy `essay`/`essay_text` question, which the scheme prices as an
            # essay rather than dropping out of the paper's total.
            counts[ESSAY_CANVAS] = counts.get(ESSAY_CANVAS, 0) + 1
    return counts


def build_weights(
    question_types: Mapping[str, Any] | None,
    total_questions: int,
    by_type: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Points per question, scaled so the paper totals exactly 100.

    Every question of a type carries that type's marks before scaling, so two
    matching questions are worth the same as each other — which is what makes the
    scheme readable and the marks defensible.
    """
    types = question_types or {}
    raw = [
        marks_for(types.get(str(i), DEFAULT_TYPE), by_type)
        for i in range(total_questions or 0)
    ]
    return {str(i): marks for i, marks in enumerate(normalise_to_100(raw))}


def scheme_dict(
    by_type: Mapping[str, Any] | None = None, partial: bool = True
) -> dict[str, Any]:
    """The scheme as it is stored, with every type it prices named explicitly.

    Named rather than implied, so the numbers a teacher saw in the builder are the
    numbers the next release normalises — a default that changes under a saved paper
    would change that paper's marks.
    """
    named = {t: marks_for(t, by_type) for t in SCHEME_ORDER}
    for t in (ESSAY, ESSAY_TEXT):
        if by_type and t in by_type:
            named[t] = marks_for(t, by_type)
    return {"by_type": named, "partial": bool(partial), "version": 1}


def with_scheme(
    weights: Mapping[str, Any] | None,
    by_type: Mapping[str, Any] | None = None,
    partial: bool = True,
) -> dict[str, Any]:
    """The weights to store, carrying the scheme beside them."""
    stored = {
        str(k): v for k, v in (weights or {}).items()
        if str(k).isdigit()
    }
    stored[SCHEME_KEY] = scheme_dict(by_type, partial)
    return stored


def weights_for(
    question_types: Mapping[str, Any] | None,
    total_questions: int,
    by_type: Mapping[str, Any] | None = None,
    partial: bool = True,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The scheme's own points per question, plus the scheme beside them.

    The server's answer to \"what does this paper's mark scheme come to\". The save
    route calls this instead of trusting the page, so a browser whose copy of the
    arithmetic is stale or wrong cannot write marks into a paper.

    `existing` keeps a scheme's own decisions where the teacher did not touch them:
    a type absent from `by_type` keeps the marks already stored for it, which is how
    editing one field of a scheme does not reset the others.
    """
    previous = (scheme_in(existing) or {}).get("by_type") or {}
    merged = {**previous, **(by_type or {})}
    return with_scheme(build_weights(question_types, total_questions, merged),
                       merged, partial)


def describe(
    question_types: Mapping[str, Any] | None,
    total_questions: int,
    by_type: Mapping[str, Any] | None = None,
    partial: bool = True,
) -> dict[str, Any]:
    """The scheme as a table, for the builder's preview and for the tests.

    Both numbers are reported per row on purpose. `marks` is what a teacher typed and
    `scaled` is what the paper will award, and showing only the first is how a scheme
    of 96 quietly becomes a score out of 104.
    """
    counts = type_counts(question_types, total_questions)
    weights = build_weights(question_types, total_questions, by_type)
    types = question_types or {}

    rows = []
    for t in SCHEME_ORDER:
        count = counts.get(t, 0)
        if not count:
            continue
        marks = marks_for(t, by_type)
        indexes = [str(i) for i in range(total_questions or 0)
                   if canonical_type(types.get(str(i), DEFAULT_TYPE)) == t]
        if t == ESSAY_CANVAS:
            # The legacy essay names are priced as essays, so their points belong to
            # this row too rather than disappearing from the table's total.
            indexes = [str(i) for i in range(total_questions or 0)
                       if canonical_type(types.get(str(i), DEFAULT_TYPE))
                       in (ESSAY_CANVAS, ESSAY_TEXT, ESSAY)]
        scaled = [float(weights.get(i, 0) or 0) for i in indexes]
        rows.append({
            "type": t,
            "kind": kind_label(t),
            "count": len(indexes),
            "marks": marks,
            "raw_subtotal": round(marks * len(indexes), 4),
            "scaled_each": round(sum(scaled) / len(scaled), 4) if scaled else 0.0,
            "scaled_subtotal": round(sum(scaled), 4),
        })

    return {
        "rows": rows,
        "weights": weights,
        "raw_total": round(sum(r["raw_subtotal"] for r in rows), 4),
        "total": round(sum(r["scaled_subtotal"] for r in rows), 4),
        "partial": bool(partial),
        "by_type": {r["type"]: r["marks"] for r in rows},
    }
