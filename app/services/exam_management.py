"""Scheduling, sitting protection and point scales for the exam editor."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.utils.exam_window import parse_dt
from app.utils.helpers import row_or_none

SITTING_LOCKED = "Siswa sudah mulai mengerjakan. Jadwal mulai, SEB, dan penghapusan dikunci."


def school_timezone_offset(supabase, school_id, fallback=7):
    if not school_id:
        return fallback
    try:
        school = row_or_none(supabase.table("schools").select("tz_offset")
                             .eq("id", school_id).maybe_single().execute()) or {}
        offset = school.get("tz_offset")
        return fallback if offset is None else int(offset)
    except Exception:
        return fallback


def order_exams(exams, tz_offset, now=None):
    if not -23 <= tz_offset <= 23:
        tz_offset = 7
    local_zone = timezone(timedelta(hours=tz_offset))
    today = (now or datetime.now(timezone.utc)).astimezone(local_zone).date()

    def key(exam):
        start = parse_dt(exam.get("start_at"))
        if start is None:
            exam["date_group"] = "unscheduled"
            return 3, 0
        local_start = start.astimezone(local_zone)
        exam["local_start"] = local_start.strftime("%Y-%m-%d %H:%M")
        day = local_start.date()
        if day == today:
            exam["date_group"] = "today"
            return 0, start.timestamp()
        if day > today:
            exam["date_group"] = "upcoming"
            return 1, start.timestamp()
        exam["date_group"] = "previous"
        return 2, -start.timestamp()

    return sorted(exams, key=key)


def has_sittings(supabase, exam_id):
    return bool(supabase.table("submissions").select("id")
                .eq("exam_id", exam_id).limit(1).execute().data)


def parse_points(raw):
    try:
        points = Decimal(str(raw).strip())
        if (points.is_finite() and Decimal("0.1") <= points <= 10000
                and points == points.quantize(Decimal("0.1"))):
            return float(points)
    except InvalidOperation:
        pass
    raise ValueError("Total poin harus 0,1–10.000 dengan maksimal satu angka desimal.")
