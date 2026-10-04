"""The transitions of a sitting, in the order they happened, for a staff reader.

`attempt_session_events` (migration 035) has been written since the audit-trail phase:
:func:`app.services.attempt_status.record_event` stamps one row per transition —
`attempt_started`, `connection_gap`, `connection_resumed`, `attempt_locked`,
`attempt_resumed`, `attempt_finalized` — and
:func:`app.services.attempt_status.timeline` reads one attempt back.

What was missing was the *rendering*. ``timeline()`` had no caller, so the record a
pupil's claim is about existed and reached nobody: a teacher answering *"my connection
dropped three times"* had to open the database. This module is that read, assembled for
the two surfaces that field the claim — the exam session page (which a teacher reaches
from their own exam) and the invigilation pages (teacher and official).

Three rules, the same ones the neighbouring readers follow
---------------------------------------------------------
**The caller's authority is data, not a decision made here.** ``exam_ids`` is the set
the caller already holds — for a teacher, the exams they invigilate *or own*; for an
official, the school's — and an **empty set selects nothing**, exactly as
:func:`app.services.exam_codes.codes_for_exams` and
:func:`app.services.invigilation.retake_requests` honour it. ``school_id`` is a
**required argument**, never read from a request.

**A description is not a verdict.** Every label names a transition that happened and
nothing about intent: a dropped connection is a dropped connection, and the length of
the gap is carried beside it because that length is the whole of the claim. A kind this
release does not know is shown as itself rather than dropped, so the record cannot lose
a transition just because a label was forgotten.

**A read that fails is an empty timeline.** A dashboard that raises because its
evidence is missing is worse than one that says it has none — the same rule
:mod:`app.services.session_review` follows.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The reader-facing label per recorded kind. Bilingual because the invigilation
#: pages carry the language toggle; the session page is Indonesian-only by design and
#: reads the ``id`` half. Kept beside `attempt_status.EVENT_KINDS` on purpose: a new
#: kind that lands without a label fails `tests/unit/test_attempt_timeline.py`.
EVENT_LABELS = {
    "attempt_started": {"id": "Ujian dimulai", "en": "Sitting started"},
    "connection_gap": {"id": "Koneksi terputus", "en": "Connection dropped"},
    "connection_resumed": {"id": "Koneksi tersambung lagi", "en": "Connection back"},
    "attempt_locked": {"id": "Ujian dikunci", "en": "Sitting locked"},
    "attempt_resumed": {"id": "Ujian dibuka kembali", "en": "Sitting resumed"},
    "attempt_finalized": {"id": "Ujian difinalkan", "en": "Sitting finalised"},
}

#: The columns read from the event table. An explicit list, not ``*``, so a later
#: column cannot arrive at a template by accident.
EVENT_COLUMNS = ("attempt_id,seq,kind,occurred_at,server_at,duration_ms,"
                 "offline,question_index,meta")


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read."""
    try:
        return query.execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("attempt_timeline: read failed: %s", exc)
        return []


def describe(kind: str, lang: str = "id") -> str:
    """The label for ``kind``, or the kind itself when this release has no label.

    The fallback is the point: a transition recorded by a newer release than the one
    rendering it must still appear, named by whatever the record calls it. Dropping it
    would leave a hole in the very sequence the claim is about.
    """
    label = EVENT_LABELS.get(str(kind or ""))
    if not label:
        return str(kind or "")
    return label.get(lang) or label.get("id") or str(kind or "")


def _clock(value) -> str:
    """``HH:MM`` from a timestamp, or ``?`` when there is none."""
    text = str(value or "")
    return text[11:16] or "?"


def _gap_detail(meta: dict) -> str:
    """How long the connection was away, from the meta the writer stamped."""
    try:
        seconds = int((meta or {}).get("gap_seconds") or 0)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    if seconds >= 60:
        return f"{seconds // 60} mnt {seconds % 60:02d} dtk"
    return f"{seconds} dtk"


def entry(row: dict, lang: str = "id") -> dict:
    """One transition as a reader meets it: what, when, and how long where it matters."""
    kind = str((row or {}).get("kind") or "")
    meta = (row or {}).get("meta") or {}
    if isinstance(meta, str):
        meta = {}
    detail = _gap_detail(meta) if kind == "connection_gap" else ""
    return {
        "kind": kind,
        "seq": (row or {}).get("seq"),
        "label": describe(kind, lang),
        "at": _clock((row or {}).get("server_at") or (row or {}).get("occurred_at")),
        "detail": detail,
        "offline": bool((row or {}).get("offline")),
    }


def for_exam(supabase, school_id: str, exam_ids) -> dict:
    """``{student_id: [entry, …]}`` for the exams the caller holds, oldest first.

    ``exam_ids`` is the caller's authority already resolved by the caller. An empty set
    selects nothing on purpose: a reader who holds no exam must see no timeline, not
    every timeline. ``school_id`` names the pupils, so a timeline cannot be paired with
    a name from another school — and it is required rather than defaulted, because a
    caller that can omit it is one missing filter from reading another school's record.

    ``submissions`` carries no ``school_id`` in this schema, so the exams are the scope
    and the school is the name pairing; both are stated here rather than implied.
    """
    wanted = [str(i) for i in (exam_ids or []) if i]
    if not wanted:
        return {}

    # The whole read is guarded, not only `execute()`: a client whose transport has
    # already gone raises at the call itself, and a dashboard that 500s because its
    # evidence is missing is worse than one that says it has none.
    try:
        return _for_exam(supabase, school_id, wanted)
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("attempt_timeline: read failed: %s", exc)
        return {}


def _for_exam(supabase, school_id: str, wanted: list[str]) -> dict:
    """The unguarded body of :func:`for_exam`; the caller owns the failure rule."""
    subs = _rows(supabase.table("submissions")
                 .select("id,exam_id,student_id").in_("exam_id", wanted))
    by_attempt = {str(s["id"]): str(s.get("student_id"))
                  for s in subs if s.get("id") and s.get("student_id")}
    if not by_attempt:
        return {}

    # `attempt_session_events` has no `exam_id`: it is numbered per attempt, and the
    # attempt is the only way in. Reading it by `exam_id` would 400, degrade to empty
    # under the handler above, and leave every page looking healthy with no timeline —
    # the exact silent hole `session_review` documents.
    events = _rows(supabase.table("attempt_session_events")
                   .select(EVENT_COLUMNS)
                   .in_("attempt_id", sorted(by_attempt))
                   .order("seq"))
    if not events:
        return {}

    # The pupils are named and filtered from this school's roster, so a timeline
    # cannot be paired with a name — or a sitting — from another school even if an id
    # were ever passed wrongly. A pupil whose roster row cannot be read still keeps
    # their timeline: the record is the finding, the name is only the label on it, and
    # dropping the row would hide the very transition the claim is about.
    student_ids = sorted(set(by_attempt.values()))
    roster = _rows(supabase.table("students")
                   .select("id, profiles!inner(full_name)")
                   .eq("school_id", school_id)
                   .in_("id", student_ids))
    names = {str(r["id"]): (r.get("profiles") or {}).get("full_name") or ""
             for r in roster}

    entries: dict[str, list] = {}
    for row in events:
        student_id = by_attempt.get(str(row.get("attempt_id")))
        if not student_id:
            continue
        entries.setdefault(student_id, []).append(entry(row))

    # A pupil whose roster row is in another school does not get a timeline; but an
    # empty roster read is a *read* that failed, not a finding about the sitting, so it
    # must not empty the page. This is the one place the school is load-bearing, and it
    # is stated rather than implied.
    if names:
        entries = {sid: rows for sid, rows in entries.items() if sid in names}

    return {sid: {"name": names.get(sid, ""), "entries": rows}
            for sid, rows in entries.items()}


__all__ = ["EVENT_LABELS", "EVENT_COLUMNS", "describe", "entry", "for_exam"]