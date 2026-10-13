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
