"""What one sitting cost, as a measurement rather than a count of what was seen.

The dashboard this feeds is descriptive, so the numbers it reads have to be honest
in one specific way: **a metric nobody measured must not be written as zero.** A
zero is a finding ("this student left the screen twice"); a missing measurement is
the absence of a finding. A table that prints both as `0` teaches a teacher to read
the second as the first, and that is the only way this page can lie without a single
number being wrong.

The design follows from that:

* ``summarize`` is pure arithmetic — no clock is read inside it, so two runs over
  one sitting cannot disagree, and ``computed_at`` is stamped by the writer.
* Both clocks are kept. ``occurred_at`` is the client's reading and can be moved;
  ``server_at`` is ours. Their difference is the only evidence a device clock was
  shifted, and a summary that trusted the client clock would let a student rewrite
  when an absence happened.
* ``sources`` records which tables actually contributed rows, so a stored zero can
  be told apart from an unmeasured metric without guessing.
* A finished sitting records its summary **best-effort**: losing a summary is a gap
  in a dashboard, losing the submission is a lost exam.

Percentiles come from ``exam_report.percentiles`` — the one method this app already
owns — because a baseline whose p25 is not the report page's p25 is a baseline
nobody can check.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.anti_cheat_service import AWAY_GRACE_SECONDS
from app.services.exam_report import percentiles
from app.utils.logger import get_logger

logger = get_logger("attempt_summary")

#: Events read per attempt. Beyond this the summary is truncated and says so — a
#: silently truncated summary reads exactly like a complete one.
MAX_EVENTS = 1000

#: Below this many attempts a baseline is still served, marked as a small sample.
MIN_BASELINE_N = 8

#: How long a stored class baseline is served before it is recomputed.
BASELINE_TTL_SECONDS = 6 * 3600

#: Drift beyond this between the client clock and ours is worth flagging. The
#: school's own network lag lives well under it; a moved clock does not.
CLOCK_TOLERANCE_MS = 60_000

METHOD_VERSION = "attempt-summary/1"
BASELINE_METHOD_VERSION = "exam-class-baseline/1"

EVENT_TABLE = "attempt_session_events"
SUMMARY_TABLE = "attempt_summary"
BASELINE_TABLE = "exam_class_baseline"
VIOLATION_TABLE = "violation_logs"

#: Being away from the paper. Charged absences arrive as ``violation_logs`` rows;
#: a later event pipeline reports the same kinds with their own durations.
AWAY_KINDS = ("tab_switch", "focus_lost", "fullscreen_exit")

#: Every column a summary row carries. Kept as one list so the writer, the schema
#: test and the baseline reader cannot drift apart.
SUMMARY_COLUMNS = (
    "attempt_id", "exam_id", "student_id", "school_id",
    "events_seen", "truncated", "counts_by_kind",
    "away_count", "away_total_ms", "away_max_ms",
    "away_short_count", "away_long_count", "away_unknown_count",
    "sync_gap_count", "offline_ms", "answer_change_count", "per_question",
    "sitting_ms", "effective_active_ms",
    "clock_drift_max_ms", "clock_suspect",
    "first_event_at", "last_event_at", "sources", "method_version",
)

#: Numeric metrics a class baseline is computed for. ``per_question`` is a map and
#: ``counts_by_kind`` is a histogram, so neither has a percentile.
METRICS = (
    "away_count", "away_total_ms", "away_max_ms",
    "away_short_count", "away_long_count",
    "sync_gap_count", "offline_ms", "answer_change_count",
    "effective_active_ms",
)

#: Values that count as "unmeasured" for a metric.
_UNMEASURED = (None,)


def now_iso() -> str:
    """The writer's clock, in the shape PostgREST stores and reads back."""
    return datetime.now(timezone.utc).isoformat()


# ── reading the two row shapes ───────────────────────────────────────────────

def _parse(value):
    """A timestamp from PostgREST, a ``datetime``, or nothing."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _is_violation(row: dict) -> bool:
    """``violation_logs`` rows carry a type; session events carry a kind."""
    return "violation_type" in row


def _kind_of(row: dict) -> str:
    if _is_violation(row):
        return str(row.get("violation_type") or "violation")
    return str(row.get("kind") or "unknown")


def _when_of(row: dict):
    """The reading this row is placed on the timeline by."""
    return _parse(row.get("server_at") or row.get("created_at") or row.get("occurred_at"))


def _drift_ms(row: dict) -> int:
    """How far the client's clock sat from ours for this row.

    A ``violation_logs`` row is stamped by the server alone, so there is nothing to
    compare and its drift is a true zero rather than an unknown.
    """
    if _is_violation(row):
        return 0
    server = _parse(row.get("server_at"))
    occurred = _parse(row.get("occurred_at"))
    if server is None or occurred is None:
        return 0
    return abs(int(round((server - occurred).total_seconds() * 1000)))


def _duration_ms(row: dict):
    """The length of an absence, or None when nobody measured it."""
    if _is_violation(row):
        meta = row.get("metadata") or {}
        if not isinstance(meta, dict):
            return None
        seconds = meta.get("away_seconds")
        if seconds is None:
            return None
        try:
            return int(round(float(seconds) * 1000))
        except (TypeError, ValueError):
            return None
    value = row.get("duration_ms")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _question_index(row: dict):
    if _is_violation(row):
        return None
    return row.get("question_index")


def _effective_active_ms(sitting_ms, away_total_ms: int, unknown: int):
    """The sitting minus its measured absences — or None if it cannot be known.

    One absence of unknown length makes the working time unknowable: reporting
    ``sitting - known`` would print a number larger than the truth and present it
    as a measurement of the student's effort.
    """
    if sitting_ms is None or unknown:
        return None
    return max(0, int(sitting_ms) - away_total_ms)


# ── the summary ──────────────────────────────────────────────────────────────

def summarize(events, sources, sitting_ms=None) -> dict:
    """Measure one sitting's events into the columns ``attempt_summary`` holds.

    ``events`` mixes two shapes on purpose: the ``violation_logs`` rows that exist
    today and the ``attempt_session_events`` rows a later pipeline sends. ``sources``
    names which tables answered, because that is what turns an unmeasured metric
    into a real zero.
    """
    sources = list(sources or [])
    all_events = list(events or [])
    events_seen = len(all_events)
    window = all_events[:MAX_EVENTS]
    truncated = events_seen > MAX_EVENTS
    events_measured = EVENT_TABLE in sources

    counts_by_kind: dict[str, int] = {}
    away_count = away_total_ms = away_max_ms = 0
    away_short = away_long = away_unknown = 0
    sync_gap_count = offline_ms = answer_change_count = 0
    clock_drift_max = 0
    timestamps = []
    per_question: dict[str, int] = {}

    for row in window:
        kind = _kind_of(row)
        counts_by_kind[kind] = counts_by_kind.get(kind, 0) + 1

        when = _when_of(row)
        if when is not None:
            timestamps.append(when)
        clock_drift_max = max(clock_drift_max, _drift_ms(row))

        if kind in AWAY_KINDS:
            away_count += 1
            length = _duration_ms(row)
            if length is None:
                away_unknown += 1
            else:
                away_total_ms += length
                away_max_ms = max(away_max_ms, length)
                if length < AWAY_GRACE_SECONDS * 1000:
                    away_short += 1
                else:
                    away_long += 1
        elif kind == "sync_gap":
            sync_gap_count += 1
        elif kind == "went_offline":
            offline_ms += _duration_ms(row) or 0
        elif kind == "answer_change":
            answer_change_count += 1

        index = _question_index(row)
        if index is not None:
            key = str(index)
            per_question[key] = per_question.get(key, 0) + (_duration_ms(row) or 0)

    return {
        "events_seen": events_seen,
        "truncated": truncated,
        "counts_by_kind": counts_by_kind,
        "away_count": away_count,
        "away_total_ms": away_total_ms,
        "away_max_ms": away_max_ms,
        "away_short_count": away_short,
        "away_long_count": away_long,
        "away_unknown_count": away_unknown,
        # Measurable only once an event source reports them; until then NULL.
        "sync_gap_count": sync_gap_count if events_measured else None,
        "offline_ms": offline_ms if events_measured else None,
        "answer_change_count": answer_change_count if events_measured else None,
        "per_question": per_question or None,
        "sitting_ms": sitting_ms,
        "effective_active_ms": _effective_active_ms(sitting_ms, away_total_ms, away_unknown),
        "clock_drift_max_ms": clock_drift_max,
        "clock_suspect": clock_drift_max > CLOCK_TOLERANCE_MS,
        "first_event_at": min(timestamps).isoformat() if timestamps else None,
        "last_event_at": max(timestamps).isoformat() if timestamps else None,
        "sources": sources,
        "method_version": METHOD_VERSION,
    }


# ── the class baseline ───────────────────────────────────────────────────────

def baseline_rows(summaries, exam_id, class_id) -> list[dict]:
    """One row per metric a class actually measured, from the app's own percentiles."""
    rows = []
    for metric in METRICS:
        values = [s.get(metric) for s in summaries if s.get(metric) not in _UNMEASURED]
        if not values:
            # Nobody measured this one: a row of zeros would be a comparison the
            # class cannot supply.
            continue
        values = [float(v) for v in values]
        spread = percentiles(values)
        median = spread["p50"]
        deviations = [abs(v - median) for v in values]
        rows.append({
            "exam_id": exam_id,
            "class_id": class_id,
            "metric": metric,
            "n": len(values),
            "median": median,
            "p10": spread["p10"],
            "p25": spread["p25"],
            "p50": spread["p50"],
            "p75": spread["p75"],
            "p90": spread["p90"],
            "iqr": round((spread["p75"] or 0.0) - (spread["p25"] or 0.0), 1),
            "mad": percentiles(deviations)["p50"],
            "metric_min": min(values),
            "metric_max": max(values),
            "small_sample": len(values) < MIN_BASELINE_N,
            "method_version": BASELINE_METHOD_VERSION,
        })
    return rows


def _baseline_is_fresh(rows) -> bool:
    """Stored rows that still answer today's question, by today's rule.

    A percentile is only comparable to a percentile from the same rule, so a row
    written by an older method version is recomputed rather than served.
    """
    for row in rows:
        if row.get("method_version") != BASELINE_METHOD_VERSION:
            return False
        when = _parse(row.get("computed_at"))
        if when is None:
            return False
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - when).total_seconds()
        if age < 0 or age > BASELINE_TTL_SECONDS:
            return False
    return True


def baseline_for(supabase, exam_id, class_id) -> list[dict]:
    """This (exam, class)'s baseline, recomputed only when the stored one is stale."""
    stored = (
        supabase.table(BASELINE_TABLE).select("*")
        .eq("exam_id", exam_id).eq("class_id", class_id).execute().data
    ) or []
    if stored and _baseline_is_fresh(stored):
        return stored

    summaries = (
        supabase.table(SUMMARY_TABLE).select("*").eq("exam_id", exam_id).execute().data
    ) or []
    rows = baseline_rows(summaries, exam_id, class_id)
    if rows:
        try:
            supabase.table(BASELINE_TABLE).upsert(
                rows, on_conflict="exam_id,class_id,metric"
            ).execute()
        except Exception:  # noqa: BLE001 — derived data, never worth a 500
            logger.exception(
                "Could not store the class baseline for exam=%s class=%s", exam_id, class_id
            )
    return rows


# ── recording one attempt's summary ──────────────────────────────────────────

def sitting_ms_for(row, submission):
    """The sitting's length, from the row's start to the submission's stop."""
    start = _parse((row or {}).get("started_at") or (submission or {}).get("started_at"))
    stop = _parse((submission or {}).get("submitted_at") or (row or {}).get("submitted_at"))
    if start is None or stop is None:
        return None
    return max(0, int((stop - start).total_seconds() * 1000))


def record_for_attempt(supabase, attempt_id, exam_id, student_id,
                       school_id=None, sitting_ms=None) -> dict | None:
    """Measure one attempt from its stored events and store the summary.

    Called after the submission itself is written. Every read degrades to an empty
    list on its own, and the caller treats a failure here as a gap in a dashboard
    rather than a lost exam.
    """
    rows = []
    sources = []
    try:
        violations = (
            supabase.table(VIOLATION_TABLE).select("*")
            .eq("user_id", student_id).eq("exam_id", exam_id).execute().data
        ) or []
        rows.extend(violations)
        sources.append(VIOLATION_TABLE)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read violation_logs for attempt=%s", attempt_id)

    try:
        events = (
            supabase.table(EVENT_TABLE).select("*")
            .eq("attempt_id", attempt_id).order("seq").limit(MAX_EVENTS + 1).execute().data
        ) or []
        rows.extend(events)
        sources.append(EVENT_TABLE)
    except Exception:  # noqa: BLE001
        logger.exception("Could not read %s for attempt=%s", EVENT_TABLE, attempt_id)

    payload = summarize(rows, sources, sitting_ms=sitting_ms)
    payload.update({
        "attempt_id": attempt_id,
        "exam_id": exam_id,
        "student_id": student_id,
        "school_id": school_id,
    })
    supabase.table(SUMMARY_TABLE).upsert(payload, on_conflict="attempt_id").execute()
    return payload
