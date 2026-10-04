"""A school's assessment calendar: Mid Semester, Final Semester, Try Out, Asesmen.

Why this is a module and not queries in the routes
--------------------------------------------------
`exams.exam_type` has carried this vocabulary since migration 007 and no code
ever read it, so the gap was never the words — it was that nothing could
*schedule* a period. A period is now one row, and every read and write of it
lives here, takes the caller's ``school_id`` as a **required argument**, and never
takes a school from the request. The routes cannot forget the scope because they
are never the ones holding it, which is the same arrangement
:mod:`app.services.invigilation` uses and for the same reason.

Exactly one period runs at a time
---------------------------------
"Which period is this paper in" has to have one answer, because that answer is
what makes a teacher's page and a pupil's list name the same period. The database
enforces it with a partial unique index (migration 050), and :func:`save_period`
keeps it true by clearing the previous running period in the same school before
it turns another on. The clear is scoped to the caller's school on purpose: an
unscoped ``UPDATE ... SET is_active = FALSE`` would stop every other school's
period as a side effect of this one's edit.

A read that fails is an empty read
----------------------------------
These rows decorate pages rather than being the point of them, so a Supabase
hiccup says "no period is running" rather than answering the reader a 500 — the
same choice :mod:`app.services.invigilation` makes for its lookups.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

#: The four periods a school runs a year on, in the order a year meets them. The
#: keys are the ones migration 050's CHECK constraint accepts; the words are the
#: school's, and they live in the templates.
KINDS: tuple[str, ...] = ("mid_semester", "final_semester", "tryout", "asesmen")

#: Every reason a write can refuse, as a key the pages translate. Kept together so
#: the bilingual catalogue (`app/templates/shared/_assessment_period_reasons.html`)
#: can be checked against it: a refusal with no sentence renders as an empty alert
#: in both languages, which is a button that does nothing with no explanation.
REFUSALS = (
    "bad_kind",
    "name_required",
    "bad_dates",
    "not_found",
    "write_failed",
)


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read."""
    try:
        return query.execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("assessment_periods: read failed: %s", exc)
        return []


def window_outside_running_period(supabase, school_id: str, start_at, end_at,
                                  tz_offset_hours: int = 7):
    """``(period, outside)`` — the running period, and whether this window escapes it.

    The window is when a paper may be *sat*; the period is the date range the
    school said this sitting counts in. Nothing joined the two, so a teacher could
    file a Mid Semester paper whose window lands in another month and every page
    would still call it Mid Semester.

    ``(None, False)`` when no period is running: a school that does not use the
    calendar keeps exactly the behaviour it had, which is the only safe reading of
    "refuse an exam outside the running period" when there is no running period.

    Dates are compared as the **school's civil day**, not the stored UTC instant: a
    teacher typing 02:00 Jakarta is on the previous UTC day, and comparing UTC
    dates would refuse a window that is plainly inside. ``tz_offset_hours`` is the
    same offset the form used to convert, so the two readings cannot disagree.

    A window with no dates cannot fall outside anything — the form's own rule is
    that a paper published without dates may be sat — so it is never refused here.
    """
    try:
        period = active_period(supabase, school_id)
    except Exception as exc:                                       # noqa: BLE001
        # This runs on the exam-write path, so an unreachable store must not refuse
        # a paper: the same "a read that fails is an empty read" choice the rest of
        # this module makes, and the reason a school with no calendar never pays for
        # one. `active_period` already swallows a failed ``execute``; this covers a
        # client that fails earlier and would otherwise 500 the builder.
        logger.warning("assessment_periods: calendar check failed: %s", exc)
        return None, False
    if not period:
        return None, False

    period_start = _as_date(period.get("start_date"))
    period_end = _as_date(period.get("end_date"))
    starts = _local_date(start_at, tz_offset_hours)
    ends = _local_date(end_at, tz_offset_hours)

    if period_start and starts and starts < period_start:
        return period, True
    if period_end and ends and ends > period_end:
        return period, True
    return period, False


def _ordered_periods(periods) -> list[dict]:
    """Periods newest first, so "which one wins" is a property of the dates and
    not of the order a query happened to return them in."""
    return sorted(periods or [],
                  key=lambda p: str(p.get("start_date") or ""), reverse=True)


def _running_of(periods) -> dict | None:
    """The running period among a set already read, or ``None``."""
    for period in periods or []:
        if period.get("is_active"):
            return period
    return None


def _tag_for(target_date, periods) -> dict | None:
    """The period a paper whose window sits on ``target_date`` belongs to.

    **One rule**, shared by the write doors and the re-tag sweep, so the two can
    never disagree about which paper is whose: the period whose range contains the
    date, newest first (a paper inside two overlapping periods belongs to the later
    one — that is what makes the choice deterministic), falling back to the running
    period when none contains it, which is also what a paper with no dates gets.
    ``None`` only when nothing qualifies.
    """
    ordered = _ordered_periods(periods)
    if target_date is not None:
        for period in ordered:
            p_start = _as_date(period.get("start_date"))
            p_end = _as_date(period.get("end_date"))
            if p_start and p_end and p_start <= target_date <= p_end:
                return period
    return _running_of(ordered)


def period_for_exam(supabase, school_id: str, start_at=None, end_at=None,
                    tz_offset_hours: int = 7) -> dict | None:
    """The assessment period a paper belongs to, or ``None``.

    The period whose dates contain the paper's window — so a paper edited after its
    period ended is still filed under that period, which is the whole point of a
    tag that survives an edit — falling back to the **running** period when the
    window has no dates. `None` when the school has no period at all.

    The decision itself lives in :func:`_tag_for`, so this and
    :func:`retag_school_exams` cannot drift into two different answers.

    A read that fails tags nothing rather than raising: this runs on the exam-write
    path, and a store hiccup must not refuse a paper (the same choice
    :func:`window_outside_running_period` makes).
    """
    try:
        starts = _local_date(start_at, tz_offset_hours)
        ends = _local_date(end_at, tz_offset_hours)
    except Exception:                                              # noqa: BLE001
        starts = ends = None
    target = starts or ends
    try:
        return _tag_for(target, list_periods(supabase, school_id))
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("assessment_periods: tag read failed: %s", exc)
        return None


def retag_school_exams(supabase, school_id: str, tz_offset_hours: int = 7) -> dict:
    """Re-derive every paper's period tag after the *calendar* moved.

    A tag is derived from the paper's window and the calendar, so editing a
    period's dates or deleting a period silently makes papers stale (their window
    is no longer in the period they name) or dangling (they name a period that is
    gone). The write doors recompute the tag when the *paper* is saved; nothing
    recomputed it when the calendar changed — this is that, and it uses the same
    :func:`_tag_for` rule the write doors do.

    Scoped to the caller's school on both reads and both writes: an unscoped sweep
    would re-home another school's papers as a side effect of this one's edit.
    Only rows whose computed tag differs from the stored one are written, so a
    calendar edit that changes nothing writes nothing and the sweep is idempotent.

    A read or write that fails is reported in the result and the sweep stops — it
    must never turn a successful calendar edit into a 500. The caller has already
    written the period; this decorates that fact.
    """
    if not school_id:
        return {"ok": False, "reason": "no_school", "scanned": 0, "retagged": 0}
    # Read *strictly* rather than through `_rows`: a failed read here must not be
    # mistaken for "this school has no periods", because a sweep that believed
    # that would *clear* every tag. A failure sweeps nothing and says so.
    try:
        periods = (supabase.table("assessment_periods").select("*")
                   .eq("school_id", school_id).order("start_date", desc=True)
                   .execute().data) or []
        exams = (supabase.table("exams")
                 .select("id,start_at,end_at,assessment_period_id")
                 .eq("school_id", school_id).execute().data) or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("assessment_periods: retag read failed: %s", exc)
        return {"ok": False, "reason": "read_failed", "scanned": 0, "retagged": 0}

    # Group by the tag each paper *should* have, so a run that re-homes many
    # papers into the same period costs one write rather than one per paper.
    changes: dict = {}
    for exam in exams:
        try:
            target = _local_date(exam.get("start_at"), tz_offset_hours) \
                or _local_date(exam.get("end_at"), tz_offset_hours)
        except Exception:                                          # noqa: BLE001
            target = None
        tag = _tag_for(target, periods)
        wanted = str(tag["id"]) if tag and tag.get("id") else None
        stored = exam.get("assessment_period_id")
        stored = str(stored) if stored else None
        if stored != wanted and exam.get("id"):
            changes.setdefault(wanted, []).append(exam["id"])

    retagged = 0
    ok = True
    for wanted, ids in changes.items():
        try:
            (supabase.table("exams")
             .update({"assessment_period_id": wanted})
             .eq("school_id", school_id).in_("id", ids).execute())
            retagged += len(ids)
        except Exception as exc:                                   # noqa: BLE001
            logger.warning("assessment_periods: retag write failed: %s", exc)
            ok = False
    return {"ok": ok, "reason": "" if ok else "write_failed",
            "scanned": len(exams), "retagged": retagged}


def retag_all_schools(supabase) -> dict:
    """Re-derive every school's paper tags, so a tag written by hand is corrected.

    :func:`retag_school_exams` fixes one school when *its* calendar moved through
    the app. It cannot see a tag written by SQL, by a migration, or by any path
    that reached the table without passing a write door — those rows keep whatever
    they were given until somebody happens to edit that paper or that calendar
    again. This is the sweep for that class: every school, on a timer.

    It calls :func:`retag_school_exams` rather than re-deriving the tag itself, so
    the aggregate answer and the per-school answer can never be two rules. The
    school list is read first; a school whose sweep fails is recorded and the rest
    still run — one unreadable school must not stop the box from reconciling.

    Never raises. The caller is a background loop or a button on a status page,
    and neither is a place to surface a store error.
    """
    try:
        schools = (supabase.table("schools").select("id").execute().data) or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("assessment_periods: school list read failed: %s", exc)
        return {"ok": False, "reason": "read_failed", "schools": 0,
                "retagged": 0, "failed": []}

    total = 0
    failed: list[str] = []
    for school in schools:
        school_id = school.get("id")
        if not school_id:
            continue
        try:
            result = retag_school_exams(supabase, school_id)
        except Exception as exc:                                   # noqa: BLE001
            logger.warning("assessment_periods: sweep failed for %s: %s", school_id, exc)
            failed.append(str(school_id))
            continue
        if result.get("ok"):
            total += int(result.get("retagged") or 0)
        else:
            failed.append(str(school_id))
    return {"ok": not failed, "reason": "" if not failed else "school_failed",
            "schools": len([s for s in schools if s.get("id")]),
            "retagged": total, "failed": failed}


def periods_by_id(supabase, period_ids) -> dict[str, dict]:
    """The named periods for a set of ids, across schools, keyed by id.

    Read by **id**, not by school: the analytics report spans schools (a super
    admin's scope), and each row already carried its own period id through
    `analysis_scope.exams_in_scope`. A failed read is an empty map, for the reason
    the rest of this module gives.
    """
    wanted = [str(pid) for pid in (period_ids or []) if pid]
    if not wanted:
        return {}
    try:
        rows = _rows(supabase.table("assessment_periods").select("*")
                     .in_("id", wanted))
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("assessment_periods: period names read failed: %s", exc)
        return {}
    return {str(row["id"]): row for row in rows if row.get("id")}


def compare_by_period(rows, periods) -> list[dict]:
    """Report rows grouped by their period, newest period first.

    `rows` are per-exam report rows (each carrying ``period_id``, ``count``,
    ``mean``, ``pass_pct``, ``min``, ``max``); `periods` is the id-to-period map
    from :func:`periods_by_id`. The aggregate is weighted **by papers, not by
    exam**: two papers of ten and ninety must not average as ``(mean+mean)/2``.

    Rows with no period form one group rather than being dropped — a report that
    silently lost the untagged papers would show fewer papers than the school sat.
    """
    groups: dict = {}
    for row in rows or []:
        pid = row.get("period_id") or None
        group = groups.get(pid)
        if group is None:
            period = (periods or {}).get(pid) or {}
            group = groups[pid] = {
                "period_id": pid,
                "name": period.get("name") or "",
                "kind": period.get("kind") or "",
                "start_date": period.get("start_date") or "",
                "end_date": period.get("end_date") or "",
                "exams": 0,
                "papers": 0,
                "_weighted": 0.0,
                "_weighted_pass": 0.0,
                "best": None,
                "worst": None,
            }
        group["exams"] += 1
        count = int(row.get("count") or 0)
        group["papers"] += count
        mean = row.get("mean")
        if mean is not None and count:
            group["_weighted"] += float(mean) * count
        pass_pct = row.get("pass_pct")
        if pass_pct is not None and count:
            group["_weighted_pass"] += float(pass_pct) * count
        high, low = row.get("max"), row.get("min")
        if high is not None:
            group["best"] = high if group["best"] is None else max(group["best"], high)
        if low is not None:
            group["worst"] = low if group["worst"] is None else min(group["worst"], low)

    result: list[dict] = []
    for group in groups.values():
        papers = group["papers"]
        result.append({
            "period_id": group["period_id"],
            "name": group["name"],
            "kind": group["kind"],
            "start_date": group["start_date"],
            "end_date": group["end_date"],
            "exams": group["exams"],
            "papers": papers,
            "mean": round(group["_weighted"] / papers, 1) if papers else None,
            "pass_rate": round(group["_weighted_pass"] / papers, 1) if papers else None,
            "best": group["best"],
            "worst": group["worst"],
        })
    # Newest period first; the untagged group has no dates, so it falls to the end.
    result.sort(key=lambda g: (str(g["start_date"] or ""), str(g["period_id"] or "")),
                reverse=True)
    return result


def _as_date(value):
    """A DATE column as a ``datetime.date``, whatever the read handed back."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def _local_date(value, tz_offset_hours: int):
    """A stored UTC window instant as the school's civil date, or ``None``."""
    if value is None or value == "":
        return None
    from app.utils.exam_window import parse_dt

    parsed = parse_dt(value)
    if parsed is None:
        return None
    return (parsed + timedelta(hours=tz_offset_hours)).date()


def list_periods(supabase, school_id: str) -> list[dict]:
    """Every period of this school, newest first.

    Scoped by ``school_id`` in the query rather than filtered afterwards: a
    school's calendar is the whole of what this page shows, and a filter applied
    after the read is one refactor away from being dropped.
    """
    if not school_id:
        return []
    return _rows(supabase.table("assessment_periods").select("*")
                 .eq("school_id", school_id).order("start_date", desc=True))


def active_period(supabase, school_id: str) -> dict | None:
    """The one period running now, or ``None``.

    Read by the school and the flag together, so this cannot return another
    school's running period even if two rows somehow carry the flag.
    """
    if not school_id:
        return None
    rows = _rows(supabase.table("assessment_periods").select("*")
                 .eq("school_id", school_id).eq("is_active", True).limit(1))
    return rows[0] if rows else None


def save_period(supabase, school_id: str, *, period_id: str | None,
                kind: str, name: str, start_date: str, end_date: str,
                is_active: bool = False, actor_id: str | None = None) -> dict:
    """Create or edit one period, or refuse with a reason.

    Every refusal happens **before** any write: a partial write would leave the
    deputy with a calendar they cannot see the shape of, which is harder to
    explain than a refusal.

    A successful write ends by re-deriving every paper's period tag
    (:func:`retag_school_exams`): the tag is derived from the calendar, so moving
    the calendar must move the tags with it, whether this call scheduled a new
    period, changed one's dates, or moved which one is running. The result is
    attached under ``retag`` rather than merged into ``ok`` — the period *was*
    written, and a sweep that could not read the exams must not report the edit as
    refused.
    """
    kind = (kind or "").strip()
    name = (name or "").strip()
    start_date = (start_date or "").strip()
    end_date = (end_date or "").strip()

    if kind not in KINDS:
        return {"ok": False, "reason": "bad_kind"}
    if not name:
        return {"ok": False, "reason": "name_required"}
    if not start_date or not end_date or end_date < start_date:
        # ISO dates compare as strings in the right order, and that is exactly the
        # comparison the database's CHECK constraint makes.
        return {"ok": False, "reason": "bad_dates"}

    payload = {
        "kind": kind,
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "is_active": bool(is_active),
        "updated_at": _now(),
    }

    if period_id:
        # The row is proved to be this school's by the *update's own filter*: a
        # read-then-write would be one interleaving away from editing another
        # school's row, and an unscoped update would edit it outright.
        written = _rows(supabase.table("assessment_periods").update(payload)
                        .eq("id", period_id).eq("school_id", school_id))
        if not written:
            return {"ok": False, "reason": "not_found"}
        if is_active:
            _clear_other_active(supabase, school_id, period_id)
        out = {"ok": True, "reason": "", "period": written[0], "created": False}
        out["retag"] = retag_school_exams(supabase, school_id)
        return out

    payload["school_id"] = school_id
    if actor_id:
        payload["created_by"] = actor_id
    if is_active:
        # Cleared before the insert, not after: the partial unique index refuses a
        # second running period, so inserting first would fail rather than move it.
        _clear_other_active(supabase, school_id, keep_id=None)
    written = _rows(supabase.table("assessment_periods").insert(payload))
    if not written:
        return {"ok": False, "reason": "write_failed"}
    out = {"ok": True, "reason": "", "period": written[0], "created": True}
    out["retag"] = retag_school_exams(supabase, school_id)
    return out


def delete_period(supabase, school_id: str, period_id: str) -> dict:
    """Remove one period. Scoped by school, like every other write here.

    The papers filed under it are then re-derived: the FK's ``ON DELETE SET NULL``
    stops a tag from *dangling* on a period that is gone, but it leaves the paper
    untagged even when another period now covers its window — so the sweep re-homes
    it. A paper no period covers is left untagged, which is the honest answer.
    """
    if not period_id:
        return {"ok": False, "reason": "not_found"}
    removed = _rows(supabase.table("assessment_periods").delete()
                    .eq("id", period_id).eq("school_id", school_id))
    if removed is None:
        return {"ok": False, "reason": "write_failed"}
    out = {"ok": True, "reason": ""}
    out["retag"] = retag_school_exams(supabase, school_id)
    return out


def _clear_other_active(supabase, school_id: str, keep_id: str | None) -> None:
    """Stop whichever period of *this school* was running.

    Scoped to ``school_id`` and nothing else: a clear without that filter is one
    deputy editing their calendar and stopping every other school's period.
    """
    query = (supabase.table("assessment_periods")
             .update({"is_active": False, "updated_at": _now()})
             .eq("school_id", school_id).eq("is_active", True))
    if keep_id:
        query = query.neq("id", keep_id)
    _rows(query)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
