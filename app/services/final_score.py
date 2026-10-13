"""A pupil's final mark, as one arithmetic with its working shown.

The formula has been the same since the first release, but it lived in several
places — `exam_scoring.rescore` subtracted the penalty, `deadline_service` rebuilt
the same sum for a paper the clock ended, `export_service` read the stored numbers
back — and a number computed in three places is three numbers that agree until one
of them is edited. This module is the one place the arithmetic happens, and it
returns the *parts* rather than the answer, because a sheet that shows only
"Nilai Akhir: 0.0" is a number nobody can check:

    final = max(0, min(raw, 100) - applied_penalty)

The two inputs are deliberately separate:

* ``raw`` is what the mark scheme earned (per-type weights normalised to exactly
  100, plus any teacher per-question marks) — the work, not the punishment;
* ``applied_penalty`` is the anti-cheat ladder summed over the violations whose
  status is *applied* (an ignored violation contributes nothing).

`penalty_breakdown` turns a list of recorded violations plus their decisions into
that applied total, so the surface that *shows* the working and the surface that
*stores* the mark ask the same function.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def ladder_value(position: int, base: float) -> float:
    """What the violation at ``position`` (1-based) is worth.

    Mirror of `anti_cheat_service.calculate_graduated_penalty`'s per-step scale —
    the first offence is the warning, the second costs ``base``, the third twice
    that, and every one after three times. Kept as a position→value table rather
    than a running total so a single ignored violation can be subtracted from the
    sum without re-levelling the ones a teacher did not touch.
    """
    if position <= 1:
        return 0.0
    if position == 2:
        return float(base)
    if position == 3:
        return float(base) * 2
    return float(base) * 3


def breakdown(raw: float, penalty: float) -> dict[str, float]:
    """``{raw, penalty, final}`` for one paper — the formula, once.

    ``raw`` is capped at 100 (the paper's own total) and the result is floored at
    0: a penalty may erase a mark but never invert it, which is what a school's
    report means by "no negative marks".
    """
    raw = round(min(float(raw or 0), 100), 2)
    penalty = round(max(float(penalty or 0), 0), 2)
    return {"raw": raw, "penalty": penalty, "final": round(max(0.0, raw - penalty), 2)}


def penalty_breakdown(violations: Iterable[Mapping[str, Any]], *,
                      base: float,
                      enabled: bool = True) -> dict[str, Any]:
    """The applied penalty over recorded violations, with the decisions kept.

    ``violations`` is the anti-cheat log in time order, each entry carrying at
    least ``violation_type`` and optionally ``status`` (``"applied"`` by default —
    the historical behaviour, so a missing decision never silently forgives a
    charge) and an ``id``. Only the kinds the ladder charges
    (``PENALIZED_VIOLATION_TYPES``) are priced; the rest are recorded, never
    billed — the same rule `anti_cheat_service._as_event` applies.

    Returns ``applied``/``ignored`` counts, the ``total``, and ``rows`` — one entry
    per charged violation with its position, its ladder value and its status — so a
    report can print why the total is what it is.
    """
    from app.services.anti_cheat_service import PENALIZED_VIOLATION_TYPES

    charged = [v for v in (violations or []) if v.get("violation_type") in PENALIZED_VIOLATION_TYPES]
    rows: list[dict[str, Any]] = []
    total = 0.0
    applied = ignored = 0
    for position, event in enumerate(charged, start=1):
        status = (event.get("status") or "applied")
        value = ladder_value(position, base) if enabled else 0.0
        counted = status == "applied"
        if counted:
            applied += 1
            total += value
        else:
            ignored += 1
        rows.append({
            "id": event.get("id"),
            "position": position,
            "violation_type": event.get("violation_type"),
            "value": round(value, 2),
            "status": "applied" if counted else "ignored",
            "reason": event.get("reason"),
            "decided_by": event.get("decided_by"),
            "decided_at": event.get("decided_at"),
        })
    return {
        "total": round(min(total, 100) if enabled else 0.0, 2),
        "applied": applied,
        "ignored": ignored,
        "recorded": len(charged),
        "rows": rows,
    }


def _number(value) -> float:
    """A stored number as a float; anything unreadable is 0, never a crash."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def display_parts(submission: Mapping[str, Any] | None) -> dict[str, Any]:
    """The numbers a result sheet shows, read from a stored submission row.

    A *display* reading. Where a caller already holds the exact picture
    (`penalty_service.inspect` — the same reader the teacher's panel and the
    pupil's page use) it passes that instead; this is the fallback for a surface
    that has only the row, so a report card, a printed PDF and a dashboard cannot
    each assemble the same facts differently.

    ``raw = final + penalty`` is the exact inverse of the arithmetic above whenever
    the 0-floor did not bite — i.e. for every paper whose penalty did not exceed
    its score. `submissions` has no column for a floored paper's raw, and a sheet
    must not invent one: a paper the floor reached (``final == 0``) reports the
    penalty it was charged against, which is the bound its mark reflects.
    """
    submission = submission or {}
    penalty = _number(submission.get("penalty"))
    final = submission.get("final_score")
    if final is None:
        final = submission.get("score")
    final = _number(final)
    # An exact `raw` the route attached (``penalty_service.working_for_exam``) wins:
    # for a paper the 0-floor reached, `final + penalty` would overstate the mark, and
    # only the mark scheme knows the real one.
    raw = submission.get("raw")
    if raw is None:
        raw = final + penalty
    else:
        raw = _number(raw)
    applied = submission.get("applied")
    recorded = submission.get("recorded")
    if recorded is None:
        recorded = int(_number(submission.get("violations")))
    if applied is None:
        # No decision is stored on the row itself, so the default reads as the
        # historical one: every recorded violation applied.
        applied = recorded
    return {
        "raw": round(_number(raw), 2),
        "penalty": round(penalty, 2),
        "final": round(final, 2),
        "recorded": int(_number(recorded)),
        "applied": int(_number(applied)),
        "ignored": int(max(0, _number(recorded) - _number(applied))),
    }


def for_submission(raw: float, applied_penalty: float,
                   violations: Iterable[Mapping[str, Any]] = (), *,
                   base: float = 5.0, enabled: bool = True) -> dict[str, Any]:
    """The whole picture one surface shows: the score, the penalty and the why.

    A single call so the printed sheet, the teacher's marking view and the pupil's
    page cannot assemble the same three numbers differently.
    """
    penalty = penalty_breakdown(violations, base=base, enabled=enabled)
    parts = breakdown(raw, applied_penalty)
    return {
        "raw": parts["raw"],
        "penalty": parts["penalty"],
        "final": parts["final"],
        "violations_recorded": penalty["recorded"],
        "violations_applied": penalty["applied"],
        "violations_ignored": penalty["ignored"],
        "violation_rows": penalty["rows"],
    }
