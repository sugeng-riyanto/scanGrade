"""How far a school's assessment has come, in the school's own calendar.

The head and the deputy asked a question the exam-scoped reports do not answer:
*what has this month actually looked like?* A list of papers tells a reader what
exists; it does not tell them that week 6 was busy and week 7 was not, that one
teacher has twenty papers waiting to be marked, or that the month's sittings
cluster on three days. Those are shapes, so they are counted, not described.

Three rules the whole module holds to:

**The clock is the school's, not the reader's and not UTC.** Every bucket is
built from a timestamp converted with the session's own offset, because a sitting
submitted at 17:30 UTC belongs to *the next day* in WIB — the day a school's
calendar prints. A report built on UTC days is wrong by a few hours' worth of
sittings and looks perfectly fine.

**A bucket with nothing in it is a zero row, not a missing one.** The calendar
grid draws every day of the month, the week list draws every week of the window,
and the month list draws every month of the window. A gap in a series is read as
"there is no data about that period", which is a different claim from "nothing
happened", and only one of them is true.

**Nothing here decides anything.** It counts, it averages, and it names the
numbers in both languages so a page can re-translate them when the toggle moves.
There is no score, no ranking and no verdict: the reader is the head of the
school, and the arithmetic is theirs to read.
"""
from __future__ import annotations

import calendar as _calendar
import datetime as dt
import logging
import math
from datetime import timezone
from typing import Any, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

#: How many months and weeks the page shows by default. Six months is a school
#: term; eight weeks is two months of weekly detail — enough to see a shape,
#: small enough that the page is one screen on a laptop.
DEFAULT_MONTHS = 6
DEFAULT_WEEKS = 8

#: The statuses in which a sitting has arrived. `draft` is an attempt that was
#: opened and not handed in, so it is counted as *started* and never as submitted:
#: counting it as handed-in would make a school's participation look complete
#: halfway through an exam.
SUBMITTED_STATUSES = ("submitted", "graded", "published")

#: A sitting whose status is exactly this has arrived and has no final mark yet —
#: the marking backlog a head of school is entitled to see. `draft` is not here:
#: an unfinished attempt is not waiting for a teacher.
WAITING_STATUSES = ("submitted",)

#: What this module reads. Named rather than `*` for the reason the rest of the
#: app names them: PostgREST reads a column left out of a select as *absent* and
#: never as an error, so a forgotten column changes a number in silence.
#:
#: `school_id` was exactly that defect, live: the reader narrows the query by the
#: reader's school **and** filters the rows it got back, and the filter read a
#: column this list did not name — so every row's `school_id` arrived as `None`,
#: every row was dropped, and the page answered a perfectly honest-looking empty
#: month for a school with four papers in it. Nothing failed: the units said
#: zero. A guard in the suite now demands that every column the reader filters on
#: is named here, because "absent" and "unset" are the same value in Python.
EXAM_COLUMNS = "id,title,subject,teacher_id,status,created_at,passing_score,school_id"
SUBMISSION_COLUMNS = ("exam_id,student_id,status,final_score,score,"
                      "started_at,submitted_at,submitted_late")

#: Exams per round-trip when the sittings are fetched in bulk, and the most
#: sittings this page will ever count. Both are the same trade the statistics page
#: makes: a cap that is printed rather than applied quietly.
CHUNK = 25
MAX_EXAMS = 120
MAX_SITTINGS = 6000

MONTH_NAMES_ID = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
                  "Agustus", "September", "Oktober", "November", "Desember")
MONTH_NAMES_EN = ("January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December")


# ── time ─────────────────────────────────────────────────────────────────────

def _parse(value: Any) -> dt.datetime | None:
    """An ISO stamp as a datetime, or None. Accepts `Z`, offsets, and BOTH the
    `+00:00` and the bare form Postgres can hand back."""
    if isinstance(value, dt.datetime):
        return value
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def local_date(value: Any, tz_offset_hours: int = 7) -> dt.date | None:
    """The day this stamp falls on in the school's clock.

    A naive stamp is read as UTC — that is how the database stores them — and an
    unreadable one is *no day at all* rather than today, because a bucket that
    quietly absorbs its bad row is a bucket that disagrees with the raw data with
    nothing to show for it.
    """
    moment = _parse(value)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone(dt.timedelta(hours=int(tz_offset_hours)))).date()


def month_key(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def parse_month_key(value: Any, fallback: dt.date) -> tuple[int, int]:
    """`"2026-02"` as (2026, 2); anything else as the fallback month.

    A hand-typed parameter must not be able to make the page error, and it must
    not be able to make it show another school's period either — a bad key simply
    means "the month I would have shown anyway".
    """
    text = str(value or "").strip()
    if len(text) == 7 and text[4] == "-":
        try:
            year, month = int(text[:4]), int(text[5:])
            if 1 <= month <= 12 and 1900 <= year <= 2999:
                return year, month
        except ValueError:
            pass
    return fallback.year, fallback.month


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    last = _calendar.monthrange(int(year), int(month))[1]
    return dt.date(int(year), int(month), 1), dt.date(int(year), int(month), last)


def month_label(year: int, month: int) -> dict[str, str]:
    return {"id": f"{MONTH_NAMES_ID[month - 1]} {year}",
            "en": f"{MONTH_NAMES_EN[month - 1]} {year}"}


def _day_label(day: dt.date) -> str:
    """`2 Feb` — the day and a short month, the same words in either language.

    Deliberately not translated: a two-day range is a date, and the twelve month
    abbreviations would be twelve more pairs on a page whose point is the grid.
    """
    return f"{day.day} {MONTH_NAMES_EN[day.month - 1][:3]}"


def _week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


# ── the shapes ───────────────────────────────────────────────────────────────

def levels(counts: Sequence[int]) -> list[int]:
    """How dark each cell paints, 0–4, on a scale set by the busiest cell.

    The scale is relative, because absolute bands say nothing on a school that
    measures in hundreds: a hundred sittings and two hundred sittings would both
    be "busiest". It is a *step* rather than `count / max`, because a flat set —
    every day the same — should sit mid-scale; dividing by the maximum paints a
    perfectly even month at the top of the scale, which is the one reading the
    grid must not give.
    """
    numbers = [max(0, int(count)) for count in counts]
    top = max(numbers) if numbers else 0
    if top <= 0:
        return [0 for _ in numbers]
    step = max(1, math.ceil(top / 4))
    return [0 if count <= 0 else min(4, 1 + (count - 1) // step) for count in numbers]


def calendar_month(day_counts: Mapping[str, Mapping[str, int]],
                   year: int, month: int) -> list[list[dict[str, Any] | None]]:
    """The month as weeks of seven, Monday first, honouring the month's margins.

    A cell the month does not own is `None` — a margin, drawn as nothing. A cell
    it owns is a day, even a day with no activity: those two are different facts
    and a grid that painted them the same would make the first of the month
    unreadable.
    """
    first, last = month_bounds(year, month)
    grid_start = _week_start(first)
    grid_end = _week_start(last) + dt.timedelta(days=6)

    days: list[dt.date] = []
    day = grid_start
    while day <= grid_end:
        days.append(day)
        day += dt.timedelta(days=1)

    counts = [int((day_counts.get(day.isoformat()) or {}).get("sittings") or 0)
              for day in days]
    shades = levels(counts)

    weeks: list[list[dict[str, Any] | None]] = []
    for index in range(0, len(days), 7):
        row: list[dict[str, Any] | None] = []
        for offset in range(7):
            position = index + offset
            day = days[position]
            if not (first <= day <= last):
                row.append(None)
                continue
            entry = day_counts.get(day.isoformat()) or {}
            row.append({
                "date": day.isoformat(),
                "day": day.day,
                "sittings": int(entry.get("sittings") or 0),
                "submitted": int(entry.get("submitted") or 0),
                "exams": int(entry.get("exams") or 0),
                "level": shades[position],
            })
        weeks.append(row)
    return weeks


def _counts_for(sittings: Iterable[Mapping[str, Any]], exams: Iterable[Mapping[str, Any]],
                tz_offset_hours: int) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Two day-indexed tallies: sittings (by the day they started) and papers.

    A sitting is counted on the day it **started**, and its submitted side on the
    day it *arrived* — the two are the same day for almost every paper, and a
    paper handed in at midnight belongs in the calendar on the day it was written
    and in the weekly totals on the day it was handed in. Keeping one tally would
    have to choose, and either choice is wrong for one of the two readers.
    """
    days: dict[str, dict[str, int]] = {}

    def entry(key: str) -> dict[str, int]:
        return days.setdefault(key, {"sittings": 0, "submitted": 0, "exams": 0})

    for sitting in sittings:
        started = local_date(sitting.get("started_at"), tz_offset_hours)
        if started is not None:
            entry(started.isoformat())["sittings"] += 1
        if str(sitting.get("status") or "") in SUBMITTED_STATUSES:
            arrived = local_date(sitting.get("submitted_at"), tz_offset_hours)
            if arrived is not None:
                entry(arrived.isoformat())["submitted"] += 1
    for exam in exams:
        created = local_date(exam.get("created_at"), tz_offset_hours)
        if created is not None:
            entry(created.isoformat())["exams"] += 1
    return days, days


def _bucket(days: Mapping[str, Mapping[str, int]], sitting_marks: Mapping[str, list[float]],
            late_days: Mapping[str, int], start: dt.date, end: dt.date) -> dict[str, Any]:
    """One period's totals, from the day tallies it covers."""
    totals = {"exams": 0, "sittings": 0, "submitted": 0, "late": 0}
    marks: list[float] = []
    day = start
    while day <= end:
        key = day.isoformat()
        entry = days.get(key) or {}
        totals["exams"] += int(entry.get("exams") or 0)
        totals["sittings"] += int(entry.get("sittings") or 0)
        totals["submitted"] += int(entry.get("submitted") or 0)
        totals["late"] += int(late_days.get(key) or 0)
        marks.extend(sitting_marks.get(key) or [])
        day += dt.timedelta(days=1)
    return {**totals, "mean": round(sum(marks) / len(marks), 1) if marks else None}


def _mark_days(sittings: Iterable[Mapping[str, Any]], tz_offset_hours: int,
               ) -> tuple[dict[str, list[float]], dict[str, int]]:
    """Marks and late papers, indexed by the day each paper *arrived*."""
    marks: dict[str, list[float]] = {}
    late: dict[str, int] = {}
    for sitting in sittings:
        if str(sitting.get("status") or "") not in SUBMITTED_STATUSES:
            continue
        arrived = local_date(sitting.get("submitted_at"), tz_offset_hours)
        if arrived is None:
            continue
        key = arrived.isoformat()
        value = sitting.get("final_score")
        if value is None:
            value = sitting.get("score")
        try:
            if value is not None and value != "":
                marks.setdefault(key, []).append(float(value))
        except (TypeError, ValueError):
            pass
        if sitting.get("submitted_late"):
            late[key] = late.get(key, 0) + 1
    return marks, late


def weekly(sittings: Sequence[Mapping[str, Any]], exams: Sequence[Mapping[str, Any]],
           tz_offset_hours: int = 7, *, today: dt.date | None = None,
           weeks: int = DEFAULT_WEEKS) -> list[dict[str, Any]]:
    """The last *weeks* ISO weeks, oldest first, one row per week.

    The window ends with the week `today` falls in and is walked backwards, so a
    reader opening the page on a Sunday and again on a Monday sees the same weeks
    with the new one appended rather than the whole axis moving under them.
    """
    today = today or dt.date.today()
    days, _ = _counts_for(sittings, exams, tz_offset_hours)
    marks, late = _mark_days(sittings, tz_offset_hours)

    this_monday = _week_start(today)
    rows: list[dict[str, Any]] = []
    for offset in range(int(weeks) - 1, -1, -1):
        start = this_monday - dt.timedelta(weeks=offset)
        end = start + dt.timedelta(days=6)
        iso_year, iso_week, _ = start.isocalendar()
        rows.append({
            "key": f"{iso_year}-W{iso_week:02d}",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": {"id": f"{_day_label(start)} – {_day_label(end)}",
                      "en": f"{_day_label(start)} – {_day_label(end)}"},
            **_bucket(days, marks, late, start, end),
        })
    return rows


def monthly(sittings: Sequence[Mapping[str, Any]], exams: Sequence[Mapping[str, Any]],
            tz_offset_hours: int = 7, *, today: dt.date | None = None,
            months: int = DEFAULT_MONTHS) -> list[dict[str, Any]]:
    """The last *months* calendar months, oldest first, one row per month."""
    today = today or dt.date.today()
    days, _ = _counts_for(sittings, exams, tz_offset_hours)
    marks, late = _mark_days(sittings, tz_offset_hours)

    rows: list[dict[str, Any]] = []
    for offset in range(int(months) - 1, -1, -1):
        year, month = _shift_month(today.year, today.month, -offset)
        start, end = month_bounds(year, month)
        rows.append({
            "key": month_key(year, month),
            "year": year,
            "month": month,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": month_label(year, month),
            **_bucket(days, marks, late, start, end),
        })
    return rows


# ── the reader ───────────────────────────────────────────────────────────────

def _chunks(values: Sequence[str], size: int = CHUNK):
    for index in range(0, len(values), size):
        yield list(values[index:index + size])


def _read_sittings(supabase, exam_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Every sitting in scope, in as few round-trips as Supabase allows.

    One query per exam is a hundred round-trips on a box with one core, which is
    the cost `analysis_scope` already pays its own way around. A failure returns
    what was read rather than raising: a page that counts half a school is worse
    than one that says it could not read, but a page that 500s says nothing at
    all — so the failure is logged and the caller states its own totals.
    """
    rows: list[dict[str, Any]] = []
    for chunk in _chunks([str(exam) for exam in exam_ids]):
        if len(rows) >= MAX_SITTINGS:
            break
        try:
            part = (supabase.table("submissions").select(SUBMISSION_COLUMNS)
                    .in_("exam_id", chunk).execute().data or [])
        except Exception as exc:                                    # noqa: BLE001
            logger.warning("official insight: could not read sittings: %s", exc)
            continue
        rows.extend(part)
    return rows[:MAX_SITTINGS]


def _read_names(supabase, user_ids: Sequence[str]) -> dict[str, str]:
    wanted = [str(value) for value in user_ids if value]
    if not wanted:
        return {}
    try:
        rows = (supabase.table("profiles").select("id,full_name")
                .in_("id", sorted(set(wanted))).execute().data or [])
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("official insight: could not read teacher names: %s", exc)
        return {}
    return {str(row.get("id")): str(row.get("full_name") or "") for row in rows}


def _empty(year: int, month: int, months: int, weeks: int, today: dt.date) -> dict[str, Any]:
    """The page's shape with nothing in it — one code path, so an empty school
    and a school mid-term render through the same template."""
    grid = calendar_month({}, year, month)
    return {
        "month": {"key": month_key(year, month), "year": year, "month": month,
                  "start": month_bounds(year, month)[0].isoformat(),
                  "end": month_bounds(year, month)[1].isoformat(),
                  "label": month_label(year, month),
                  "prev": month_key(*_shift_month(year, month, -1)),
                  "next": month_key(*_shift_month(year, month, 1))},
        "calendar": {"weeks": grid, "days": sum(1 for row in grid for cell in row if cell),
                     "total": 0, "busiest": 0, "label": month_label(year, month)},
        "weeks": weekly([], [], 7, today=today, weeks=weeks),
        "months": monthly([], [], 7, today=today, months=months),
        "teachers": [],
        "totals": {"exams": 0, "sittings": 0, "submitted": 0, "late": 0,
                   "waiting": 0, "mean": None},
        "no_data": True,
    }


def progress(supabase, school_id: str | None, *, today: dt.date | None = None,
             tz_offset_hours: int = 7, month: str | None = None,
             months: int = DEFAULT_MONTHS, weeks: int = DEFAULT_WEEKS) -> dict[str, Any]:
    """A school's month and its recent weeks, as the page reads them.

    The scope is the caller's own `school_id`, and there is no parameter that
    could widen it: a page for a head of school that accepted a school from the
    query string would be a page that reads another school's children. A caller
    with no school gets the empty shape rather than every school's rows.
    """
    today = today or dt.date.today()
    year, shown = parse_month_key(month, today)
    if not school_id:
        return _empty(year, shown, months, weeks, today)

    # The window the reads cover: the union of the months on the trend line and
    # the month on screen, so paging back through the calendar is a query, never a
    # second page that quietly reads nothing.
    window_start = min(month_bounds(*_shift_month(today.year, today.month, -(int(months) - 1)))[0],
                       month_bounds(year, shown)[0])
    window_end = max(today, month_bounds(year, shown)[1])

    try:
        exams = (supabase.table("exams").select(EXAM_COLUMNS)
                 .eq("school_id", school_id)
                 .gte("created_at", f"{window_start.isoformat()}T00:00:00+00:00")
                 .order("created_at", desc=True)
                 .limit(MAX_EXAMS).execute().data or [])
    except Exception as exc:                                        # noqa: BLE001
        logger.warning("official insight: could not read exams: %s", exc)
        exams = []
    exams = [exam for exam in exams
             if str(exam.get("school_id") or "") == str(school_id)][:MAX_EXAMS]

    sittings = _read_sittings(supabase, [exam["id"] for exam in exams])
    names = _read_names(supabase, [exam.get("teacher_id") for exam in exams])

    days, _ = _counts_for(sittings, exams, tz_offset_hours)
    marks, late = _mark_days(sittings, tz_offset_hours)

    start, end = month_bounds(year, shown)
    grid = calendar_month(days, year, shown)
    cells = [cell for row in grid for cell in row if cell]
    shown_bucket = _bucket(days, marks, late, start, end)

    # The month's own per-teacher table. Read from the same tallies as the page's
    # totals, so a teacher's column adds up to the school's row rather than to a
    # separately-computed number that happens to be close.
    by_teacher: dict[str, dict[str, Any]] = {}
    for exam in exams:
        created = local_date(exam.get("created_at"), tz_offset_hours)
        if created is None or not (start <= created <= end):
            continue
        entry = by_teacher.setdefault(str(exam.get("teacher_id") or ""), {
            "exams": 0, "sittings": 0, "submitted": 0, "waiting": 0, "marks": []})
        entry["exams"] += 1
    exam_owner = {str(exam["id"]): str(exam.get("teacher_id") or "") for exam in exams}
    for sitting in sittings:
        owner = exam_owner.get(str(sitting.get("exam_id")), "")
        status = str(sitting.get("status") or "")
        started = local_date(sitting.get("started_at"), tz_offset_hours)
        arrived = local_date(sitting.get("submitted_at"), tz_offset_hours)
        entry = by_teacher.get(owner)
        if entry is None:
            continue
        if started is not None and start <= started <= end:
            entry["sittings"] += 1
        if status in SUBMITTED_STATUSES and arrived is not None and start <= arrived <= end:
            entry["submitted"] += 1
            value = sitting.get("final_score")
            if value is None:
                value = sitting.get("score")
            try:
                if value is not None and value != "":
                    entry["marks"].append(float(value))
            except (TypeError, ValueError):
                pass
        if status in WAITING_STATUSES and started is not None and start <= started <= end:
            entry["waiting"] += 1
    teachers = [{"id": teacher_id or "",
                 "name": names.get(teacher_id) or "",
                 "exams": entry["exams"], "sittings": entry["sittings"],
                 "submitted": entry["submitted"], "waiting": entry["waiting"],
                 "mean": (round(sum(entry["marks"]) / len(entry["marks"]), 1)
                          if entry["marks"] else None)}
                for teacher_id, entry in by_teacher.items()]
    # A table is read from the top, and the reader's question is "who has the most
    # waiting" — so the busiest backlog is first and the name breaks its ties.
    teachers.sort(key=lambda row: (-row["waiting"], -row["submitted"], row["name"].lower()))

    waiting = sum(1 for sitting in sittings
                  if str(sitting.get("status") or "") in WAITING_STATUSES
                  and (local_date(sitting.get("started_at"), tz_offset_hours) or dt.date.min)
                  and start <= (local_date(sitting.get("started_at"), tz_offset_hours)
                                or dt.date.min) <= end)

    all_marks: list[float] = []
    for key, values in marks.items():
        if start.isoformat() <= key <= end.isoformat():
            all_marks.extend(values)

    totals = {"exams": shown_bucket["exams"], "sittings": shown_bucket["sittings"],
              "submitted": shown_bucket["submitted"], "late": shown_bucket["late"],
              "waiting": waiting,
              "mean": (round(sum(all_marks) / len(all_marks), 1) if all_marks else None)}

    return {
        "month": {"key": month_key(year, shown), "year": year, "month": shown,
                  "start": start.isoformat(), "end": end.isoformat(),
                  "label": month_label(year, shown),
                  "prev": month_key(*_shift_month(year, shown, -1)),
                  "next": month_key(*_shift_month(year, shown, 1))},
        "calendar": {"weeks": grid, "days": len(cells),
                     "total": sum(cell["sittings"] for cell in cells),
                     "busiest": max((cell["sittings"] for cell in cells), default=0),
                     "label": month_label(year, shown)},
        "weeks": weekly(sittings, exams, tz_offset_hours, today=today, weeks=weeks),
        "months": monthly(sittings, exams, tz_offset_hours, today=today, months=months),
        "teachers": teachers,
        "totals": totals,
        "no_data": totals["sittings"] == 0 and totals["submitted"] == 0,
    }
