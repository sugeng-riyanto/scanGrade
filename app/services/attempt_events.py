"""What the page saw while a pupil was on the paper, numbered in its own band.

`attempt_session_events` has been written by one caller since migration 035: the
server's own transitions — a detected gap, a resume, a lock, a media play. The
table's comment says the rest waits for the ingest phase, and until it lands
`attempt_summary.sync_gap_count`, `offline_ms`, `answer_change_count` and
`per_question` stay `NULL`, which is why every dashboard built on them is blank.
This module is that phase's writer.

Two rules decide its shape:

* **The client numbers its own stream, in a band the server's transitions cannot
  reach.** The table's uniqueness is `UNIQUE (attempt_id, seq)`, and
  `attempt_status.record_event` numbers transitions from `max(seq) + 1`. A page
  numbering itself from the same space could take a number a transition just took;
  the database would refuse one of the two, silently. So client events start at
  :data:`CLIENT_EVENT_SEQ_BASE`, and a seq below it is refused rather than written
  into the space the server owns.

* **A retry is free.** The page flushes batches over a school network, so the same
  batch arrives twice. Idempotency is the database's `UNIQUE (attempt_id, seq)`,
  not the client's promise: the insert is sent with `ignore_duplicates`, and a
  replay writes nothing.

This is deliberately **not** a lock trigger. It records what happened and decides
nothing at all; locking stays fullscreen/tab-switch, as `attempt_status` says.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.utils.logger import get_logger

logger = get_logger("attempt_events")

#: The table one row per event lives in.
EVENT_TABLE = "attempt_session_events"

#: Where the client's own numbering starts. Far above any transition
#: `attempt_status.record_event` writes (`1, 2, 3, …`), so the two writers cannot
#: collide on `UNIQUE (attempt_id, seq)`. Ordering in the timeline is by
#: `server_at`, which is the only clock the server set itself.
CLIENT_EVENT_SEQ_BASE = 1_000_000

#: The kinds the page may report. A closed set, because a `kind` nobody defined is
#: a string some later reader would have to interpret — and `meta` is where the
#: detail of a known kind goes, not a second vocabulary.
CLIENT_EVENT_KINDS = (
    "tab_hidden",            # the document stopped being visible
    "tab_visible",           # …and came back
    "window_blur",
    "window_focus",
    "went_offline",          # the browser lost the network
    "came_online",
    "question_changed",      # the pupil moved to another question
    "answer_changed",        # the pupil edited an answer
    "control_unavailable",   # the device cannot offer a control (e.g. Fullscreen on iOS)
    "orientation_shift",     # the screen was turned portrait<->landscape
)

#: One flush cannot be unbounded: the box is 1 vCPU and a page left open could
#: otherwise post an unbounded body.
MAX_EVENTS_PER_BATCH = 200

#: An absence longer than this is not an absence any paper could contain, so it is
#: a client bug or a fabricated value, not a measurement.
MAX_DURATION_MS = 8 * 60 * 60 * 1000

#: `meta` is context, not a payload. A generous cap that still cannot hold a page.
MAX_META_BYTES = 1024

#: The columns the door needs to prove the sitting is the caller's.
ATTEMPT_COLUMNS = "id,status,started_at"


def _now(now=None) -> datetime:
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _as_int(value):
    """An int, or None. `True` is not an int here: it is a boolean and a mistake."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _valid(event, index: int) -> tuple[bool, str, dict | None]:
    """`(ok, reason, row)` for one client event. Reason is a key, not a sentence."""
    if not isinstance(event, dict):
        return False, "not_an_object", None

    seq = _as_int(event.get("seq"))
    if seq is None or seq < CLIENT_EVENT_SEQ_BASE:
        return False, "seq", None

    kind = event.get("kind")
    if kind not in CLIENT_EVENT_KINDS:
        return False, "kind", None

    duration = event.get("duration_ms")
    if duration is not None:
        duration = _as_int(duration)
        if duration is None or duration < 0 or duration > MAX_DURATION_MS:
            return False, "duration", None

    question = event.get("question_index")
    if question is not None:
        question = _as_int(question)
        if question is None or question < 0:
            return False, "question", None

    meta = event.get("meta")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        return False, "meta", None
    try:
        if len(json.dumps(meta)) > MAX_META_BYTES:
            return False, "meta", None
    except (TypeError, ValueError):
        return False, "meta", None

    occurred = event.get("occurred_at")
    if occurred is not None and not isinstance(occurred, str):
        occurred = None                  # a client clock we cannot read is no clock

    device = event.get("device_class")
    row = {
        "seq": seq,
        "kind": kind,
        "occurred_at": occurred,
        "duration_ms": duration,
        "offline": bool(event.get("offline")),
        "question_index": question,
        "meta": meta,
    }
    if isinstance(device, str) and device:
        row["device_class"] = device[:24]
    return True, "", row


def record_batch(supabase, attempt_id, events, *, now=None) -> dict:
    """Store one flush of client events. Best-effort, never raising.

    Returns ``{"stored": n, "rejected": [{"index": i, "reason": key}], "error": None}``.
    A rejected event is dropped, not the batch: one malformed entry must not cost
    the pupil the rest of the flush. A write that fails is logged and reported —
    the audit trail must never cost a pupil their paper, the same rule
    `submission_service` follows for its summaries.
    """
    out = {"stored": 0, "rejected": [], "error": None}
    if not attempt_id or not events:
        return out
    if len(events) > MAX_EVENTS_PER_BATCH:
        # The whole flush is refused rather than truncated: a silently truncated
        # batch is indistinguishable from a complete one at the reader.
        out["rejected"].append({"index": 0, "reason": "too_many"})
        return out

    rows, when = [], _now(now)
    for index, event in enumerate(events):
        ok, reason, row = _valid(event, index)
        if not ok:
            out["rejected"].append({"index": index, "reason": reason})
            continue
        row.update({"attempt_id": attempt_id, "server_at": when.isoformat()})
        rows.append(row)

    if not rows:
        return out
    try:
        # `ignore_duplicates` is the whole retry story: a batch the page already
        # delivered is replayed with the same seqs, and the database's
        # `UNIQUE (attempt_id, seq)` drops every one of them.
        supabase.table(EVENT_TABLE).upsert(
            rows, on_conflict="attempt_id,seq", ignore_duplicates=True).execute()
    except Exception:  # noqa: BLE001 — derived evidence, never a pupil's paper
        logger.warning("could not store %d client event(s) for attempt %s",
                       len(rows), attempt_id)
        out["error"] = "db"
        return out
    out["stored"] = len(rows)
    return out


def open_attempt(supabase, exam_id, student_id):
    """The caller's sitting on this paper, or None.

    The attempt is `submissions.id` for one (student, exam) — a pair the database
    already keeps unique (`submissions_student_exam_unique`). A read that fails is
    None and a log line, not an exception at a page: the event door must not 500
    because a reader hiccupped.
    """
    if not exam_id or not student_id:
        return None
    try:
        rows = (
            supabase.table("submissions").select(ATTEMPT_COLUMNS)
            .eq("exam_id", exam_id).eq("student_id", student_id)
            .order("created_at", desc=True).limit(1).execute().data
        ) or []
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the sitting for exam=%s", exam_id)
        return None
    return rows[0] if rows else None


def sitting_status(supabase, exam_id, student_id, row):
    """The status of this sitting, from the one function that owns the vocabulary.

    Imported here rather than re-derived, because a second spelling of
    active/finished/expired is exactly what `attempt_status` exists to prevent.
    """
    from app.services import attempt_status
    return attempt_status.get_attempt_status(supabase, exam_id, student_id, row=row)
