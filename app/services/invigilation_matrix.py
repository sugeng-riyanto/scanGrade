"""The invigilator matrix: who stands in which room, in which slot, on which day.

One row per cell
----------------
`invigilation_duty` is the cell of the matrix migration 059 describes: a teacher, a
room, a period and a date. The two unique constraints on that table are the
conflict rules themselves — a room holds one teacher per slot, and a teacher stands
in one room per slot — and this module checks them *before* the write so the
operator reads *"Budi already has a duty in Sesi 1"* rather than a constraint
error, while the index stays the thing that actually holds under two simultaneous
POSTs.

Why every function takes ``school_id``
--------------------------------------
The same reason :mod:`app.services.invigilation` does. The backend uses the service
key and walks straight through RLS, so scope lives in the query, and a query can
only forget a filter that was written at the call site. Here it is a **required
argument** of every read and write, and it is never read from a request.

The importer is one function, and it is pure
--------------------------------------------
The bulk path is the one place a matrix can be corrupted quickly, so the parsing
and per-row validation live in :func:`parse_workbook` — a pure function over the
school's own periods, rooms and teachers — and the commit lives in
:func:`apply_rows`. A test can drive forty rows through the parser without a
database, and the same function the test exercises is the one the route calls.

The uploaded workbook is not kept
---------------------------------
Nothing here ever writes the file anywhere. The route reads it into memory,
:func:`parse_workbook` parses it from those bytes, and the bytes are dropped when
the request ends — no path on disk, no object in Storage. What survives is the
structured duties and one audit line naming who uploaded and how many rows landed.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

#: Every reason a write can refuse, as a key the pages translate. Kept together so
#: the bilingual catalogue (`shared/_invigilation_reasons.html`) can be checked
#: against it — a refusal with no sentence renders as an empty alert.
REFUSALS = (
    "name_required",
    "duplicate_name",
    "period_not_in_school",
    "room_not_in_school",
    "teacher_not_in_school",
    "room_taken",
    "teacher_busy",
    "bad_date",
    "not_found",
    "write_failed",
    "bad_file",
    "no_rows",
    "nothing_to_apply",
    "no_classes",
)

#: The header a template carries, in the order it is written. The normaliser strips
#: case, spaces and punctuation, so a school that retypes them still matches.
TEMPLATE_HEADERS = ("Tanggal", "Periode", "Nama Ruangan", "Email Guru", "Catatan")

#: The marker in the example row. A row that carries it is skipped, never counted
#: as an error: the school was told to delete it, and forgetting is not a mistake
#: worth a red row.
EXAMPLE_MARKER = "CONTOH"

#: The reference sheet's name, so the template's two tabs are one story.
REFERENCE_SHEET = "Referensi"

#: The sessions a school gets when rooms are seeded from its classes. Named here,
#: not built at the call site, so the page, the seeder and the reference sheet all
#: say the same three words.
DEFAULT_SESSION_NAMES = ("Sesi 1", "Sesi 2", "Sesi 3")

#: A cell value Excel hands back that is not a date and not a period name.
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def _rows(query) -> list[dict]:
    """``execute().data`` or nothing — a read that fails is an empty read."""
    try:
        return query.execute().data or []
    except Exception as exc:                                       # noqa: BLE001
        logger.warning("invigilation_matrix: read failed: %s", exc)
        return []


def _norm(value) -> str:
    """A name reduced to lowercase alphanumerics — the way columns are matched."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _clean(value) -> str:
    return str(value if value is not None else "").strip()


# ── the caller's own periods and rooms ──────────────────────────────────────

def list_periods(supabase, school_id: str, *, active_only: bool = False) -> list[dict]:
    """This school's slots, in the order the school put them."""
    query = (supabase.table("exam_period")
             .select("id, name, sort_order, starts_at, ends_at, is_active")
             .eq("school_id", school_id).order("sort_order").order("name"))
    if active_only:
        query = query.eq("is_active", True)
    return _rows(query)


def list_rooms(supabase, school_id: str, *, active_only: bool = False) -> list[dict]:
    """This school's rooms, by name."""
    query = (supabase.table("exam_room")
             .select("id, name, capacity, is_active")
             .eq("school_id", school_id).order("name"))
    if active_only:
        query = query.eq("is_active", True)
    return _rows(query)


def save_period(supabase, school_id: str, *, period_id: str | None = None,
                name: str = "", sort_order=None, starts_at: str = "",
                ends_at: str = "") -> dict:
    """Create or rename one slot. The name is the key, so it must not collide."""
    name = _clean(name)
    if not name:
        return {"ok": False, "reason": "name_required"}
    try:
        order = int(sort_order) if sort_order not in (None, "") else 0
    except (TypeError, ValueError):
        order = 0

    payload = {"school_id": school_id, "name": name, "sort_order": order,
               "starts_at": _clean(starts_at) or None,
               "ends_at": _clean(ends_at) or None,
               "updated_at": datetime.utcnow().isoformat()}

    clash = _rows(supabase.table("exam_period").select("id, name")
                  .eq("school_id", school_id))
    for row in clash:
        if _norm(row.get("name")) == _norm(name) and str(row.get("id")) != str(period_id):
            return {"ok": False, "reason": "duplicate_name"}

    if period_id:
        written = _rows(supabase.table("exam_period").update(payload)
                        .eq("id", period_id).eq("school_id", school_id))
    else:
        written = _rows(supabase.table("exam_period").insert(payload))
    return {"ok": bool(written), "reason": "" if written else "write_failed",
            "period": written[0] if written else None}


def save_room(supabase, school_id: str, *, room_id: str | None = None,
              name: str = "", capacity=None) -> dict:
    """Create or rename one room, with its capacity if the school knows it."""
    name = _clean(name)
    if not name:
        return {"ok": False, "reason": "name_required"}
    try:
        cap = int(capacity) if capacity not in (None, "") else None
    except (TypeError, ValueError):
        cap = None

    payload = {"school_id": school_id, "name": name, "capacity": cap,
               "updated_at": datetime.utcnow().isoformat()}

    clash = _rows(supabase.table("exam_room").select("id, name")
                  .eq("school_id", school_id))
    for row in clash:
        if _norm(row.get("name")) == _norm(name) and str(row.get("id")) != str(room_id):
            return {"ok": False, "reason": "duplicate_name"}

    if room_id:
        written = _rows(supabase.table("exam_room").update(payload)
                        .eq("id", room_id).eq("school_id", school_id))
    else:
        written = _rows(supabase.table("exam_room").insert(payload))
    return {"ok": bool(written), "reason": "" if written else "write_failed",
            "room": written[0] if written else None}


# ── seeding the axes from the school's own classes ─────────────────────────

def class_rooms(supabase, school_id: str) -> list[dict]:
    """This school's classes, each with the number of pupils it holds.

    A class is where the candidates already sit, so it is the honest *default* exam
    room: the room takes the class's name and its capacity is the roster size —
    the number the capacity chip is actually for. The count is one read over every
    class (a ``class_id`` in-filter), not one read per class, so a school with
    thirty classes still pays a single round-trip.
    """
    classes = _rows(supabase.table("classes")
                    .select("id, name, grade_level")
                    .eq("school_id", school_id).order("name"))
    ids = [str(c["id"]) for c in classes]
    counts: dict[str, int] = {}
    if ids:
        pupils = _rows(supabase.table("profiles").select("class_id")
                       .eq("role", "murid").eq("school_id", school_id)
                       .in_("class_id", ids))
        for pupil in pupils:
            cid = str(pupil.get("class_id"))
            counts[cid] = counts.get(cid, 0) + 1
    for c in classes:
        c["pupil_count"] = counts.get(str(c["id"]), 0)
    return classes


def seed_from_classes(supabase, school_id: str) -> dict:
    """Create one room per class and the three sessions — only the missing ones.

    Called by a **button**, never by a GET, so merely opening the page writes
    nothing. Each room keeps its class's name and its capacity is that class's roster
    size. Anything already present — matched the same case-and-punctuation-blind way
    the importer matches names — is left alone, so pressing the button twice is not
    two rooms called "7A".
    """
    classes = class_rooms(supabase, school_id)
    if not classes:
        return {"ok": False, "reason": "no_classes", "rooms": 0, "periods": 0,
                "seeded": []}

    existing_rooms = {_norm(r.get("name")) for r in list_rooms(supabase, school_id)}
    existing_periods = {_norm(p.get("name")) for p in list_periods(supabase, school_id)}
    seeded: list[dict] = []
    rooms_made = 0
    for klass in classes:
        name = _clean(klass.get("name"))
        if not name or _norm(name) in existing_rooms:
            continue
        out = save_room(supabase, school_id, name=name,
                        capacity=klass.get("pupil_count"))
        if out.get("ok"):
            rooms_made += 1
            existing_rooms.add(_norm(name))
            seeded.append({"kind": "room", "name": name,
                           "capacity": klass.get("pupil_count")})

    periods_made = 0
    for index, name in enumerate(DEFAULT_SESSION_NAMES, start=1):
        if _norm(name) in existing_periods:
            continue
        out = save_period(supabase, school_id, name=name, sort_order=index)
        if out.get("ok"):
            periods_made += 1
            existing_periods.add(_norm(name))
            seeded.append({"kind": "period", "name": name})

    return {"ok": True, "reason": "", "rooms": rooms_made,
            "periods": periods_made, "seeded": seeded}


# ── the matrix for one day ──────────────────────────────────────────────────

def duties_for_date(supabase, school_id: str, exam_date: str) -> list[dict]:
    """Every cell this school filled for one day."""
    return _rows(supabase.table("invigilation_duty")
                 .select("id, exam_date, period_id, room_id, teacher_id, source, notes")
                 .eq("school_id", school_id).eq("exam_date", exam_date))


def teachers_of(supabase, school_id: str) -> list[dict]:
    """This school's teachers, named, so a cell can be drawn and a dropdown filled."""
    out = []
    for row in _rows(supabase.table("teachers").select("id, profiles!inner(full_name)")
                     .eq("school_id", school_id)):
        out.append({"id": str(row["id"]),
                    "name": (row.get("profiles") or {}).get("full_name") or ""})
    out.sort(key=lambda t: t["name"].lower())
    return out


def matrix(supabase, school_id: str, exam_date: str) -> dict:
    """The whole grid for one day: periods (rows), rooms (columns), filled cells.

    Three reads plus one for the names, not one per cell: a school with eight slots
    and twelve rooms would otherwise pay a hundred round-trips to draw one screen.
    """
    periods = list_periods(supabase, school_id, active_only=True)
    rooms = list_rooms(supabase, school_id, active_only=True)
    duties = duties_for_date(supabase, school_id, exam_date)
    teachers = {t["id"]: t["name"] for t in teachers_of(supabase, school_id)}

    cells: dict[str, dict[str, dict]] = {}
    load: dict[str, int] = {}
    for duty in duties:
        pid, rid = str(duty.get("period_id")), str(duty.get("room_id"))
        tid = str(duty.get("teacher_id"))
        cells.setdefault(pid, {})[rid] = {
            "id": str(duty["id"]),
            "teacher_id": tid,
            "teacher_name": teachers.get(tid, ""),
            "source": duty.get("source") or "manual",
            "notes": duty.get("notes") or "",
        }
        load[tid] = load.get(tid, 0) + 1

    return {"exam_date": exam_date, "periods": periods, "rooms": rooms,
            "cells": cells, "load": load,
            "load_named": [{"teacher_id": tid, "name": teachers.get(tid, ""),
                            "count": count}
                           for tid, count in sorted(load.items(),
                                                    key=lambda kv: (-kv[1], kv[0]))]}


def available_teachers(supabase, school_id: str, exam_date: str,
                       period_id: str) -> list[dict]:
    """Teachers with nothing in this slot — the dropdown a free cell offers.

    A teacher already standing in another room of this slot is not offered, because
    the write would refuse them; showing them and then rejecting the choice is the
    kind of form that teaches an operator to distrust it.
    """
    busy = {str(d["teacher_id"]) for d in duties_for_date(supabase, school_id, exam_date)
            if str(d.get("period_id")) == str(period_id)}
    return [t for t in teachers_of(supabase, school_id) if t["id"] not in busy]


# ── filling a cell ──────────────────────────────────────────────────────────

def assign(supabase, school_id: str, *, exam_date: str, period_id: str, room_id: str,
           teacher_id: str, notes: str = "", source: str = "manual",
           actor_id: str | None = None) -> dict:
    """Put one teacher in one cell, or say which rule refused.

    The checks below are the two unique constraints read out in words, and they are
    here for the *message*, not for the guarantee: the index is what holds when two
    writes arrive together. A room already filled is not silently overwritten —
    the operator is told, and clears the cell first.
    """
    if not _row(supabase, "exam_period", school_id, period_id):
        return {"ok": False, "reason": "period_not_in_school"}
    if not _row(supabase, "exam_room", school_id, room_id):
        return {"ok": False, "reason": "room_not_in_school"}
    if not _row(supabase, "teachers", school_id, teacher_id):
        return {"ok": False, "reason": "teacher_not_in_school"}

    existing = duties_for_date(supabase, school_id, exam_date)
    for duty in existing:
        if str(duty.get("period_id")) != str(period_id):
            continue
        if str(duty.get("room_id")) == str(room_id):
            return {"ok": False, "reason": "room_taken"}
        if str(duty.get("teacher_id")) == str(teacher_id):
            return {"ok": False, "reason": "teacher_busy"}

    payload = {"school_id": school_id, "exam_date": exam_date,
               "period_id": period_id, "room_id": room_id, "teacher_id": teacher_id,
               "source": source if source in ("manual", "excel_upload") else "manual",
               "notes": _clean(notes) or None,
               "updated_at": datetime.utcnow().isoformat()}
    if actor_id:
        payload["created_by"] = actor_id
    written = _rows(supabase.table("invigilation_duty").insert(payload))
    return {"ok": bool(written), "reason": "" if written else "write_failed",
            "duty": written[0] if written else None}


def clear_cell(supabase, school_id: str, duty_id: str) -> dict:
    """Empty one cell. Scoped by school, like every other write."""
    removed = _rows(supabase.table("invigilation_duty").delete()
                    .eq("id", duty_id).eq("school_id", school_id))
    return {"ok": bool(removed), "reason": "" if removed else "not_found"}


def set_cell(supabase, school_id: str, *, exam_date: str, period_id: str,
             room_id: str, teacher_id: str, actor_id: str | None = None) -> dict:
    """The click: put this teacher in this room of this session, or take them out.

    Clicking the cell that already holds this teacher empties it. Any other click
    *sets* it, and to keep the two rules — one teacher per room, one room per teacher
    in a session — it clears whatever stood in the way first: the room's current
    occupant and this teacher's duty elsewhere in the same session. The write is the
    same :func:`assign` the dropdown and the upload use, so a click and a spreadsheet
    cannot end up with two different sets of rules.
    """
    if not _row(supabase, "exam_period", school_id, period_id):
        return {"ok": False, "reason": "period_not_in_school", "action": ""}
    if not _row(supabase, "exam_room", school_id, room_id):
        return {"ok": False, "reason": "room_not_in_school", "action": ""}
    if not _row(supabase, "teachers", school_id, teacher_id):
        return {"ok": False, "reason": "teacher_not_in_school", "action": ""}

    existing = duties_for_date(supabase, school_id, exam_date)
    for duty in existing:
        if str(duty.get("period_id")) != str(period_id):
            continue
        if (str(duty.get("room_id")) == str(room_id)
                and str(duty.get("teacher_id")) == str(teacher_id)):
            clear_cell(supabase, school_id, duty["id"])
            return {"ok": True, "reason": "", "action": "cleared"}

    for duty in existing:
        if str(duty.get("period_id")) != str(period_id):
            continue
        if (str(duty.get("room_id")) == str(room_id)
                or str(duty.get("teacher_id")) == str(teacher_id)):
            clear_cell(supabase, school_id, duty["id"])

    out = assign(supabase, school_id, exam_date=exam_date, period_id=period_id,
                 room_id=room_id, teacher_id=teacher_id, source="manual",
                 actor_id=actor_id)
    return {"ok": bool(out.get("ok")), "reason": out.get("reason", ""), "action": "set"}


def auto_fill(supabase, school_id: str, *, exam_date: str, period_id: str,
              actor_id: str | None = None) -> dict:
    """Fill every empty room of one session with the next free teacher.

    The one-press version of the tedious part. A room already filled keeps its
    teacher, and a teacher already standing somewhere in this session is never
    offered again — so the fill can create neither conflict, and it stops when the
    rooms are full or the free teachers run out, whichever comes first.
    """
    if not _row(supabase, "exam_period", school_id, period_id):
        return {"ok": False, "reason": "period_not_in_school", "applied": 0}
    rooms = list_rooms(supabase, school_id, active_only=True)
    teachers = teachers_of(supabase, school_id)
    in_slot = [d for d in duties_for_date(supabase, school_id, exam_date)
               if str(d.get("period_id")) == str(period_id)]
    taken_rooms = {str(d.get("room_id")) for d in in_slot}
    busy = {str(d.get("teacher_id")) for d in in_slot}
    free = [t for t in teachers if t["id"] not in busy]

    applied = 0
    for room in rooms:
        if str(room["id"]) in taken_rooms or not free:
            continue
        teacher = free.pop(0)
        out = assign(supabase, school_id, exam_date=exam_date, period_id=period_id,
                     room_id=str(room["id"]), teacher_id=teacher["id"],
                     source="manual", actor_id=actor_id)
        if out.get("ok"):
            applied += 1
    return {"ok": True, "reason": "", "applied": applied}


def _row(supabase, table: str, school_id: str, row_id: str) -> dict | None:
    """The row, if it exists in this school — one query, two conditions."""
    rows = _rows(supabase.table(table).select("id")
                 .eq("id", row_id).eq("school_id", school_id))
    return rows[0] if rows else None


# ── the Excel template ──────────────────────────────────────────────────────

def build_template(periods: list[dict], rooms: list[dict]) -> bytes:
    """An empty workbook: one example row plus a reference sheet of the school's own.

    The example row is filled and marked so the shape is obvious without a manual;
    the second sheet lists this school's periods and rooms, so a school that has
    renamed "Sesi 1" to "Sesi Pagi" writes the name the importer will match.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Pengawas"

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1D4ED8")
    for column, header in enumerate(TEMPLATE_HEADERS, start=1):
        cell = ws.cell(row=1, column=column, value=header)
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center")

    first_period = periods[0]["name"] if periods else "Sesi 1"
    first_room = rooms[0]["name"] if rooms else "Ruang 1"
    example = [
        f"[{EXAMPLE_MARKER} - hapus baris ini]",
        first_period, first_room, "guru@sekolah.sch.id",
        "Contoh baris — ganti dengan data asli, lalu hapus baris ini",
    ]
    warn_fill = PatternFill("solid", fgColor="FEF3C7")
    for column, value in enumerate(example, start=1):
        cell = ws.cell(row=2, column=column, value=value)
        cell.fill = warn_fill
        cell.font = Font(italic=True, color="92400E")

    widths = (34, 18, 20, 30, 44)
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = width

    ref = wb.create_sheet(REFERENCE_SHEET)
    ref.append(["Periode", ""])
    ref["A1"].font = Font(bold=True)
    for period in periods:
        ref.append([period.get("name") or "", period.get("starts_at") or ""])
    ref.append([])
    ref.append(["Nama Ruangan", "Kapasitas"])
    ref["A" + str(ref.max_row)].font = Font(bold=True)
    for room in rooms:
        ref.append([room.get("name") or "", room.get("capacity") or ""])
    ref.column_dimensions["A"].width = 24
    ref.column_dimensions["B"].width = 14

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def is_xlsx(data: bytes) -> bool:
    """Whether the bytes are a real XLSX (a zip), not merely named ``.xlsx``.

    An extension is a claim; the signature is the fact. A renamed CSV or an image
    reaches :func:`parse_workbook` only if this passed first.
    """
    return bool(data) and data[:4] == b"PK\x03\x04"


# ── the importer, pure over the school's own lists ──────────────────────────

def _as_date(value):
    """A date from whatever Excel hands back, or ``None``.

    Excel returns a real ``datetime`` for a date cell, a string for a typed one, and
    either may arrive with a time component. All three mean the same day.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _clean(value)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_workbook(data: bytes, *, periods: dict, rooms: dict, teachers: dict,
                   taken_rooms=None, taken_teachers=None) -> dict:
    """Read the uploaded workbook into valid rows and per-row errors.

    ``periods``/``rooms``/``teachers`` are the school's own rows keyed by
    ``_norm(name)`` / ``_norm(name)`` / ``email.lower()`` — the caller builds them
    with the same functions :func:`matrix` and :func:`available_teachers` use, so the
    importer and the dropdown can never disagree about who is in this school.

    ``taken_rooms`` and ``taken_teachers`` are ``set``s of ``(date, period_id, room_id)``
    and ``(date, period_id, teacher_id)`` already filled that day. A row that only
    **repeats** a cell the file itself already claimed is also an error: two lines
    asking for the same room are one line plus a mistake.

    Never raises on a bad row — the whole file is read and every bad line is
    reported with its number, because stopping at the first error makes an operator
    fix a forty-row sheet one upload at a time.
    """
    from openpyxl import load_workbook

    try:
        wb = load_workbook(filename=io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                                              # noqa: BLE001
        return {"ok": False, "reason": "bad_file", "rows": [], "errors": [],
                "total": 0, "valid": 0}

    ws = wb["Pengawas"] if "Pengawas" in wb.sheetnames else wb[wb.sheetnames[0]]

    rows_out: list[dict] = []
    errors: list[dict] = []
    seen_rooms: set = set()
    seen_teachers: set = set()
    taken_rooms = set(taken_rooms or set())
    taken_teachers = set(taken_teachers or set())
    total = 0

    for index, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        row = tuple(row or ())
        if not any(_clean(cell) for cell in row):
            continue
        # The example row is skipped, never reported: the school was told to delete
        # it, and a red "row 2" on the row we ourselves wrote is noise.
        if any(EXAMPLE_MARKER in _clean(cell).upper() for cell in row):
            continue
        total += 1
        raw_date, raw_period, raw_room, raw_email, raw_notes = (
            list(row) + [""] * 5)[:5]

        problems = []
        exam_date = _as_date(raw_date)
        if not exam_date:
            problems.append(("bad_date", _clean(raw_date)))

        period = periods.get(_norm(raw_period))
        if not period:
            problems.append(("period_not_in_school", _clean(raw_period)))

        room = rooms.get(_norm(raw_room))
        if not room:
            problems.append(("room_not_in_school", _clean(raw_room)))

        teacher = teachers.get(_clean(raw_email).lower())
        if not teacher:
            problems.append(("teacher_not_in_school", _clean(raw_email)))

        if exam_date and period and room:
            room_key = (exam_date, str(period["id"]), str(room["id"]))
            if room_key in taken_rooms or room_key in seen_rooms:
                problems.append(("room_taken", _clean(raw_room)))
            elif teacher:
                teacher_key = (exam_date, str(period["id"]), str(teacher["id"]))
                if teacher_key in taken_teachers or teacher_key in seen_teachers:
                    problems.append(("teacher_busy", _clean(raw_email)))
                else:
                    seen_rooms.add(room_key)
                    seen_teachers.add(teacher_key)

        entry = {
            "row": index,
            "exam_date": exam_date,
            "period_id": str(period["id"]) if period else "",
            "period_name": _clean(raw_period),
            "room_id": str(room["id"]) if room else "",
            "room_name": _clean(raw_room),
            "teacher_id": str(teacher["id"]) if teacher else "",
            "teacher_email": _clean(raw_email),
            "teacher_name": (teacher or {}).get("name") or "",
            "notes": _clean(raw_notes),
            "problems": [{"reason": reason, "value": value} for reason, value in problems],
        }
        if problems:
            errors.append({"row": index, "problems": entry["problems"]})
            entry["valid"] = False
        else:
            entry["valid"] = True
        rows_out.append(entry)

    return {"ok": True, "reason": "", "rows": rows_out, "errors": errors,
            "total": total, "valid": sum(1 for r in rows_out if r["valid"])}


def apply_rows(supabase, school_id: str, rows, *, source: str = "excel_upload",
               actor_id: str | None = None) -> dict:
    """Write the parsed rows the operator confirmed, and only those.

    Rows already marked invalid are ignored here even if they are passed in, so a
    crafted POST cannot commit a line the preview showed as an error. Every write is
    the same :func:`assign` the matrix uses, so an upload and a click cannot end up
    with two different sets of rules.
    """
    applied, refused = 0, 0
    for row in rows or []:
        if not row.get("valid"):
            refused += 1
            continue
        out = assign(supabase, school_id,
                     exam_date=row.get("exam_date") or "",
                     period_id=row.get("period_id") or "",
                     room_id=row.get("room_id") or "",
                     teacher_id=row.get("teacher_id") or "",
                     notes=row.get("notes") or "",
                     source=source, actor_id=actor_id)
        if out.get("ok"):
            applied += 1
        else:
            refused += 1
    return {"ok": applied > 0, "applied": applied, "refused": refused}
