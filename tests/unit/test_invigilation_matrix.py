"""The invigilator matrix: one teacher per room per slot, and a bulk upload that
can be corrupted in one press.

The schedule (`invigilation_schedules`) answers *when a class sits a paper*. A room
full of several classes' candidates needs a different answer — *who stands in this
room during this slot* — and that is what this file pins. The failure it exists to
prevent is not a missing cell. It is:

* **a cross-school write** — the same shape every service in this repo guards
  against: the school comes from the caller, is a required argument, and every write
  carries it. A period, room or teacher from another school must be refused;
* **a silent conflict** — the two unique constraints (one teacher per slot, one room
  per slot) are rules the operator reads, and the importer must report the *same*
  refusal the click path reports, not a constraint error;
* **a corrupted bulk upload** — one bad row must not stop the file, every bad row must
  be named by its line, the example row must be skipped rather than reported, and a
  row the file itself duplicates must be caught before it reaches the database;
* **the file on disk** — the workbook is parsed from memory and never written
  anywhere, so the assertion is that the parser is a pure function of bytes;
* **a forged commit** — the apply door must re-run every row through the same
  ``assign`` the grid uses, so a preview marked invalid cannot be committed by hand.

The service's own reads are exercised against the PostgREST stand-in the schedule
tests use, so a matrix and a schedule are read through the same fake.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "invigilation_matrix.py"
MIGRATION = ROOT / "supabase" / "migrations" / "059_invigilation_matrix.sql"
CAPS = ROOT / "supabase" / "migrations" / "061_invigilation_seat_caps.sql"
ADMIN_ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"
PAGE = ROOT / "app" / "templates" / "admin_sekolah" / "invigilation_matrix.html"
REASONS = ROOT / "app" / "templates" / "shared" / "_invigilation_reasons.html"
BASE = ROOT / "app" / "templates" / "base.html"

from app.services import invigilation_matrix as im  # noqa: E402
from tests.unit.test_invigilation import _DB  # noqa: E402  (the same PostgREST stand-in)

SCHOOL = "s1"
OTHER = "s2"


def _tables() -> dict:
    return {
        "schools": [{"id": SCHOOL}, {"id": OTHER}],
        "exam_period": [
            {"id": "p1", "school_id": SCHOOL, "name": "Sesi 1", "sort_order": 1,
             "starts_at": "07:30", "ends_at": "09:30", "is_active": True},
            {"id": "p2", "school_id": SCHOOL, "name": "Sesi 2", "sort_order": 2,
             "starts_at": "10:00", "ends_at": "12:00", "is_active": True},
            {"id": "p9", "school_id": OTHER, "name": "Sesi 1", "sort_order": 1,
             "is_active": True},
        ],
        "exam_room": [
            {"id": "r1", "school_id": SCHOOL, "name": "Ruang 1", "capacity": 30,
             "is_active": True},
            {"id": "r2", "school_id": SCHOOL, "name": "Ruang 2", "capacity": 24,
             "is_active": True},
            {"id": "r9", "school_id": OTHER, "name": "Ruang 1", "capacity": 20,
             "is_active": True},
        ],
        "teachers": [
            {"id": "t1", "school_id": SCHOOL, "profiles": {"full_name": "Bu Sari"}},
            {"id": "t2", "school_id": SCHOOL, "profiles": {"full_name": "Pak Budi"}},
            {"id": "t9", "school_id": OTHER, "profiles": {"full_name": "Asing"}},
        ],
        "invigilation_duty": [],
    }


def _duty(duty_id="d1", *, date="2026-10-01", period="p1", room="r1", teacher="t1",
              source="manual"):
    return {"id": duty_id, "school_id": SCHOOL, "exam_date": date, "period_id": period,
            "room_id": room, "teacher_id": teacher, "source": source, "notes": None}


def _workbook(rows, *, headers=im.TEMPLATE_HEADERS, sheet="Pengawas") -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _lists(*, periods=("Sesi 1",), rooms=("Ruang 1",), emails=("bu@sekolah.sch.id",)):
    period_map = {im._norm(name): {"id": p["id"], "name": name}
                  for name, p in zip(periods, _tables()["exam_period"])}
    room_map = {im._norm(name): {"id": r["id"], "name": name}
                for name, r in zip(rooms, _tables()["exam_room"])}
    teacher_map = {email.lower(): {"id": f"t{i + 1}", "name": f"Guru {i + 1}"}
                   for i, email in enumerate(emails)}
    return period_map, room_map, teacher_map


# ── 1. the schema ───────────────────────────────────────────────────────────

class TestTheSchema:
    def test_the_migration_is_additive_and_idempotent(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql), "the migration drops something"
        assert not re.search(r"^\s*BEGIN\s*;", sql, re.M), "the runner owns the transaction"
        assert not re.search(r"^\s*COMMIT\s*;", sql, re.M)
        for _ in re.findall(r"CREATE TABLE (?!IF NOT EXISTS)", sql):
            raise AssertionError("a table is created twice-run-unsafe")
        for policy in re.findall(r"CREATE POLICY \"([^\"]+)\"", sql):
            assert f'DROP POLICY IF EXISTS "{policy}"' in sql, (
                f"{policy} is created without being dropped first")

    def test_the_base_migration_pins_one_duty_per_cell(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "invigilation_duty_room_slot_key UNIQUE (school_id, exam_date, period_id, room_id)" in sql, (
            "a room could hold two invigilators in one slot")
        assert "invigilation_duty_teacher_slot_key UNIQUE (school_id, exam_date, period_id, teacher_id)" in sql, (
            "a teacher could stand in two rooms in one slot")

    def test_the_caps_migration_relaxes_those_to_two(self):
        sql = CAPS.read_text(encoding="utf-8")
        # The two single-occupancy constraints are gone, a repeat of the very same
        # cell is still impossible, and the "at most two" limit is a trigger —
        # UNIQUE cannot express a ceiling of two.
        assert "DROP CONSTRAINT IF EXISTS invigilation_duty_room_slot_key" in sql
        assert "DROP CONSTRAINT IF EXISTS invigilation_duty_teacher_slot_key" in sql
        assert ("invigilation_duty_cell_key" in sql
                and "UNIQUE (school_id, exam_date, period_id, room_id, teacher_id)" in sql)
        assert "invigilation_duty_seat_cap" in sql
        assert "room_count >= 2" in sql and "teacher_count >= 2" in sql
        assert "BEFORE INSERT OR UPDATE ON public.invigilation_duty" in sql
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql), "the migration drops something"
        assert not re.search(r"^\s*BEGIN\s*;", sql, re.M), "the runner owns the transaction"

    def test_every_table_carries_its_own_school_and_rls(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        for table in ("exam_period", "exam_room", "invigilation_duty"):
            assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in sql
        assert sql.count("school_id UUID NOT NULL REFERENCES public.schools(id)") >= 3


# ── 2. the axes ─────────────────────────────────────────────────────────────

class TestTheAxes:
    def test_a_period_needs_a_name(self):
        db = _DB(_tables())
        out = im.save_period(db, SCHOOL, name="   ")
        assert out == {"ok": False, "reason": "name_required"}, out
        assert db.tables["exam_period"] == _tables()["exam_period"]

    def test_a_duplicate_period_name_is_refused(self):
        db = _DB(_tables())
        out = im.save_period(db, SCHOOL, name="sesi 1")
        assert out["reason"] == "duplicate_name", out

    def test_a_period_is_written_for_the_callers_school(self):
        db = _DB(_tables())
        out = im.save_period(db, SCHOOL, name="Sesi 3", sort_order=3)
        assert out["ok"] and out["period"]["school_id"] == SCHOOL

    def test_a_room_is_written_for_the_callers_school(self):
        db = _DB(_tables())
        out = im.save_room(db, SCHOOL, name="Ruang 3", capacity=40)
        assert out["ok"] and out["room"]["school_id"] == SCHOOL
        assert out["room"]["capacity"] == 40

    def test_a_duplicate_room_name_is_refused(self):
        db = _DB(_tables())
        out = im.save_room(db, SCHOOL, name="ruang 1")
        assert out["reason"] == "duplicate_name", out


# ── 3. filling a cell ───────────────────────────────────────────────────────

class TestFillingACell:
    def test_a_duty_is_written_for_the_callers_school(self):
        db = _DB(_tables())
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                        room_id="r1", teacher_id="t1", actor_id="a1")
        assert out["ok"], out
        stored = db.tables["invigilation_duty"][0]
        assert stored["school_id"] == SCHOOL
        assert stored["created_by"] == "a1"
        assert stored["source"] == "manual"

    def test_another_schools_period_is_refused(self):
        db = _DB(_tables())
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p9",
                        room_id="r1", teacher_id="t1")
        assert out == {"ok": False, "reason": "period_not_in_school"}, out
        assert db.tables["invigilation_duty"] == []

    def test_another_schools_room_is_refused(self):
        db = _DB(_tables())
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                        room_id="r9", teacher_id="t1")
        assert out["reason"] == "room_not_in_school", out

    def test_another_schools_teacher_is_refused(self):
        db = _DB(_tables())
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                        room_id="r1", teacher_id="t9")
        assert out["reason"] == "teacher_not_in_school", out

    def test_a_room_takes_a_second_teacher(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", teacher="t1", room="r1"))
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                        room_id="r1", teacher_id="t2")
        assert out["ok"], out
        assert len(db.tables["invigilation_duty"]) == 2

    def test_a_room_with_two_teachers_refuses_a_third(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"] = [
            _duty("d1", teacher="t1", room="r1"),
            _duty("d2", teacher="t2", room="r1"),
        ]
        db.tables["teachers"].append({"id": "t3", "school_id": SCHOOL,
                                      "profiles": {"full_name": "Bu Tiga"}})
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                        room_id="r1", teacher_id="t3")
        assert out["reason"] == "room_full", out

    def test_a_teacher_takes_a_second_room(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", teacher="t1", room="r1"))
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                        room_id="r2", teacher_id="t1")
        assert out["ok"], out
        assert len(db.tables["invigilation_duty"]) == 2

    def test_a_teacher_in_two_rooms_refuses_a_third(self):
        db = _DB(_tables())
        db.tables["exam_room"].append({"id": "r3", "school_id": SCHOOL, "name": "Ruang 3",
                                       "capacity": 20, "is_active": True})
        db.tables["invigilation_duty"] = [
            _duty("d1", teacher="t1", room="r1"),
            _duty("d2", teacher="t1", room="r2"),
        ]
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                        room_id="r3", teacher_id="t1")
        assert out["reason"] == "teacher_full", out

    def test_the_very_same_cell_twice_is_not_a_second_seat(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", teacher="t1", room="r1"))
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                        room_id="r1", teacher_id="t1")
        assert out["reason"] == "already_assigned", out

    def test_the_same_teacher_may_take_a_later_slot(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", period="p1", teacher="t1"))
        out = im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p2",
                        room_id="r1", teacher_id="t1")
        assert out["ok"], out

    def test_every_duty_write_carries_the_school_filter(self):
        db = _DB(_tables())
        im.assign(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                  room_id="r1", teacher_id="t1")
        writes = [e for e in db.log if e[1] == "invigilation_duty" and e[0] != "select"]
        assert writes, "nothing was written"
        for _op, _table, payload, _filters in writes:
            assert payload.get("school_id") == SCHOOL


# ── 4. reading the grid ─────────────────────────────────────────────────────

class TestTheGrid:
    def test_the_grid_is_scoped_to_the_callers_school(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"] = [
            _duty("d1"),
            {"id": "d9", "school_id": OTHER, "exam_date": "2026-10-01", "period_id": "p9",
             "room_id": "r9", "teacher_id": "t9", "source": "manual"},
        ]
        grid = im.matrix(db, SCHOOL, "2026-10-01")
        assert "p1" in grid["cells"], "this school's own cell is missing"
        assert "p9" not in grid["cells"], "another school's duty leaked into the grid"

    def test_a_cell_carries_the_teachers_name(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", teacher="t1"))
        grid = im.matrix(db, SCHOOL, "2026-10-01")
        assert grid["cells"]["p1"]["r1"][0]["teacher_name"] == "Bu Sari"

    def test_a_room_cell_lists_both_of_its_teachers(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"] = [
            _duty("d1", teacher="t1", room="r1"),
            _duty("d2", teacher="t2", room="r1"),
        ]
        grid = im.matrix(db, SCHOOL, "2026-10-01")
        names = {c["teacher_name"] for c in grid["cells"]["p1"]["r1"]}
        assert names == {"Bu Sari", "Pak Budi"}, names

    def test_the_load_counts_each_teacher(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"] = [
            _duty("d1", period="p1", room="r1", teacher="t1"),
            _duty("d2", period="p2", room="r1", teacher="t1"),
            _duty("d3", period="p1", room="r2", teacher="t2"),
        ]
        grid = im.matrix(db, SCHOOL, "2026-10-01")
        assert grid["load"]["t1"] == 2 and grid["load"]["t2"] == 1

    def test_available_teachers_include_one_with_a_spare_seat(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", period="p1", teacher="t1"))
        free = im.available_teachers(db, SCHOOL, "2026-10-01", "p1")
        assert [t["id"] for t in free] == ["t1", "t2"], free

    def test_available_teachers_drop_one_who_is_full(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"] = [
            _duty("d1", period="p1", teacher="t1", room="r1"),
            _duty("d2", period="p1", teacher="t1", room="r2"),
        ]
        free = im.available_teachers(db, SCHOOL, "2026-10-01", "p1")
        assert [t["id"] for t in free] == ["t2"], free


# ── 5. clearing a cell ──────────────────────────────────────────────────────

class TestClearingACell:
    def test_a_cell_is_emptied_with_the_school_filter(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1"))
        out = im.clear_cell(db, SCHOOL, "d1")
        assert out["ok"] and db.tables["invigilation_duty"] == []

    def test_another_schools_cell_is_not_touched(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(
            {"id": "d9", "school_id": OTHER, "exam_date": "2026-10-01", "period_id": "p9",
             "room_id": "r9", "teacher_id": "t9", "source": "manual"})
        out = im.clear_cell(db, SCHOOL, "d9")
        assert out == {"ok": False, "reason": "not_found"}, out
        assert len(db.tables["invigilation_duty"]) == 1

    def test_a_missing_cell_reports_not_found(self):
        db = _DB(_tables())
        assert im.clear_cell(db, SCHOOL, "nope")["reason"] == "not_found"


# ── 6. the workbook: signature and template ─────────────────────────────────

class TestTheWorkbook:
    def test_an_extension_is_not_a_signature(self):
        assert im.is_xlsx(b"PK\x03\x04rest")
        assert not im.is_xlsx(b"date,period\n2026-10-01,Sesi 1")
        assert not im.is_xlsx(b"")

    def test_the_template_has_headers_an_example_row_and_a_reference_sheet(self):
        periods = _tables()["exam_period"][:2]
        rooms = _tables()["exam_room"][:2]
        data = im.build_template(periods, rooms)
        assert im.is_xlsx(data)

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data))
        ws = wb["Pengawas"]
        assert [c.value for c in ws[1]] == list(im.TEMPLATE_HEADERS)
        assert im.EXAMPLE_MARKER in str(ws.cell(row=2, column=1).value), (
            "the example row is not marked, so a school may keep it")
        assert im.REFERENCE_SHEET in wb.sheetnames
        ref = wb[im.REFERENCE_SHEET]
        names = {row[0] for row in ref.iter_rows(values_only=True)}
        assert "Sesi 1" in names and "Ruang 1" in names, (
            "the reference sheet does not list the school's own periods and rooms")


# ── 7. the importer: per-row, never stopping ────────────────────────────────

class TestTheImporter:
    def test_a_clean_row_is_valid_and_carries_its_ids(self):
        periods, rooms, teachers = _lists()
        data = _workbook([["2026-10-01", "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", "catatan"]])
        out = im.parse_workbook(data, periods=periods, rooms=rooms, teachers=teachers)
        assert out["ok"] and out["total"] == 1 and out["valid"] == 1
        row = out["rows"][0]
        assert row["period_id"] == "p1" and row["room_id"] == "r1"
        assert row["teacher_id"] == "t1" and row["notes"] == "catatan"

    def test_an_excel_date_object_is_read(self):
        from datetime import datetime

        periods, rooms, teachers = _lists()
        data = _workbook([[datetime(2026, 10, 2), "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", ""]])
        out = im.parse_workbook(data, periods=periods, rooms=rooms, teachers=teachers)
        assert out["rows"][0]["exam_date"] == "2026-10-02"

    def test_the_example_row_is_skipped_not_reported(self):
        periods, rooms, teachers = _lists()
        data = _workbook([
            [f"[{im.EXAMPLE_MARKER} - hapus baris ini]", "Sesi 1", "Ruang 1", "x@y.z", "contoh"],
            ["2026-10-01", "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", ""],
        ])
        out = im.parse_workbook(data, periods=periods, rooms=rooms, teachers=teachers)
        assert out["total"] == 1, "the example row was counted"
        assert out["errors"] == []

    def test_every_bad_row_is_named_by_its_line(self):
        periods, rooms, teachers = _lists()
        data = _workbook([
            ["bukan-tanggal", "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", ""],   # row 2
            ["2026-10-01", "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", ""],      # row 3 ok
            ["2026-10-01", "Sesi 9", "Ruang 1", "bu@sekolah.sch.id", ""],      # row 4 bad period
            ["2026-10-01", "Sesi 1", "Ruang 9", "bu@sekolah.sch.id", ""],      # row 5 bad room
            ["2026-10-01", "Sesi 1", "Ruang 1", "asing@lain.sch.id", ""],      # row 6 bad teacher
        ])
        out = im.parse_workbook(data, periods=periods, rooms=rooms, teachers=teachers)
        assert out["total"] == 5 and out["valid"] == 1
        by_row = {e["row"]: [p["reason"] for p in e["problems"]] for e in out["errors"]}
        assert "bad_date" in by_row[2]
        assert "period_not_in_school" in by_row[4]
        assert "room_not_in_school" in by_row[5]
        assert "teacher_not_in_school" in by_row[6]

    def test_a_row_with_two_problems_reports_both(self):
        periods, rooms, teachers = _lists()
        data = _workbook([["bukan-tanggal", "Sesi 9", "Ruang 1", "bu@sekolah.sch.id", ""]])
        out = im.parse_workbook(data, periods=periods, rooms=rooms, teachers=teachers)
        reasons = {p["reason"] for p in out["errors"][0]["problems"]}
        assert {"bad_date", "period_not_in_school"} <= reasons

    def test_a_room_already_holding_two_is_reported(self):
        periods, rooms, teachers = _lists()
        data = _workbook([["2026-10-01", "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", ""]])
        out = im.parse_workbook(
            data, periods=periods, rooms=rooms, teachers=teachers,
            taken_rooms={("2026-10-01", "p1", "r1"): 2})
        assert out["errors"][0]["problems"][0]["reason"] == "room_full"

    def test_a_room_holding_one_accepts_a_second_row(self):
        periods, rooms, teachers = _lists()
        data = _workbook([["2026-10-01", "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", ""]])
        out = im.parse_workbook(
            data, periods=periods, rooms=rooms, teachers=teachers,
            taken_rooms={("2026-10-01", "p1", "r1"): 1})
        assert out["valid"] == 1 and out["errors"] == []

    def test_a_teacher_already_in_two_rooms_is_reported(self):
        periods, rooms, teachers = _lists(rooms=("Ruang 1", "Ruang 2"))
        data = _workbook([["2026-10-01", "Sesi 1", "Ruang 2", "bu@sekolah.sch.id", ""]])
        out = im.parse_workbook(
            data, periods=periods, rooms=rooms, teachers=teachers,
            taken_teachers={("2026-10-01", "p1", "t1"): 2})
        assert out["errors"][0]["problems"][0]["reason"] == "teacher_full"

    def test_two_rows_in_one_file_repeating_a_cell_are_caught(self):
        periods, rooms, teachers = _lists()
        data = _workbook([
            ["2026-10-01", "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", ""],
            ["2026-10-01", "Sesi 1", "Ruang 1", "bu@sekolah.sch.id", ""],
        ])
        out = im.parse_workbook(data, periods=periods, rooms=rooms, teachers=teachers)
        assert out["valid"] == 1 and out["errors"][0]["row"] == 3
        assert out["errors"][0]["problems"][0]["reason"] == "already_assigned"

    def test_a_file_filling_one_room_to_two_is_capped_at_three(self):
        periods, rooms, teachers = _lists(emails=("a@x.id", "b@x.id", "c@x.id"))
        data = _workbook([
            ["2026-10-01", "Sesi 1", "Ruang 1", "a@x.id", ""],
            ["2026-10-01", "Sesi 1", "Ruang 1", "b@x.id", ""],
            ["2026-10-01", "Sesi 1", "Ruang 1", "c@x.id", ""],
        ])
        out = im.parse_workbook(data, periods=periods, rooms=rooms, teachers=teachers)
        assert out["valid"] == 2
        assert out["errors"][0]["problems"][0]["reason"] == "room_full"

    def test_a_non_workbook_reports_bad_file(self):
        periods, rooms, teachers = _lists()
        out = im.parse_workbook(b"not a zip at all", periods=periods, rooms=rooms,
                                teachers=teachers)
        assert out["ok"] is False and out["reason"] == "bad_file"


# ── 8. the commit: only the rows the grid would allow ───────────────────────

class TestTheCommit:
    def test_only_valid_rows_are_applied(self):
        db = _DB(_tables())
        rows = [
            {"valid": True, "exam_date": "2026-10-01", "period_id": "p1", "room_id": "r1",
             "teacher_id": "t1", "notes": ""},
            {"valid": False, "exam_date": "2026-10-01", "period_id": "p1", "room_id": "r2",
             "teacher_id": "t2", "notes": ""},
        ]
        out = im.apply_rows(db, SCHOOL, rows, actor_id="a1")
        assert out["applied"] == 1 and out["refused"] == 1
        assert len(db.tables["invigilation_duty"]) == 1

    def test_a_forged_row_is_re_proved_by_assign(self):
        """A hand-crafted apply cannot commit a foreign period: ``apply_rows`` runs
        ``assign``, which re-checks the school."""
        db = _DB(_tables())
        out = im.apply_rows(db, SCHOOL, [
            {"valid": True, "exam_date": "2026-10-01", "period_id": "p9",
             "room_id": "r1", "teacher_id": "t1", "notes": ""}])
        assert out["applied"] == 0 and out["refused"] == 1
        assert db.tables["invigilation_duty"] == []

    def test_an_applied_row_is_tagged_as_an_upload(self):
        db = _DB(_tables())
        im.apply_rows(db, SCHOOL, [
            {"valid": True, "exam_date": "2026-10-01", "period_id": "p1",
             "room_id": "r1", "teacher_id": "t1", "notes": ""}])
        assert db.tables["invigilation_duty"][0]["source"] == "excel_upload"


# ── 9. the doors ────────────────────────────────────────────────────────────

def _route_blocks(source: str) -> list[str]:
    starts = [m.start() for m in re.finditer(r"@\w*bp\.route\(", source)]
    starts.append(len(source))
    return [source[a:b] for a, b in zip(starts, starts[1:])]


MATRIX_ROUTES = (
    '"/invigilation/matrix"',
    '"/invigilation/matrix/periods"',
    '"/invigilation/matrix/rooms"',
    '"/invigilation/matrix/assign"',
    '"/invigilation/matrix/clear"',
    '"/invigilation/matrix/template.xlsx"',
    '"/invigilation/matrix/upload"',
    '"/invigilation/matrix/apply"',
    '"/invigilation/matrix/cells"',
)


class TestTheDoors:
    def test_every_matrix_route_is_on_the_admin_prefix_behind_the_admin_guard(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        for path in MATRIX_ROUTES:
            assert path in source, f"{path} is missing"
            block = next(b for b in _route_blocks(source)
                         if f"@admin_sekolah_bp.route({path}" in b)
            assert "@admin_sekolah_required" in block, f"{path} is unguarded"

    def test_the_school_comes_from_the_session_not_the_form(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        for path in MATRIX_ROUTES:
            block = next(b for b in _route_blocks(source)
                         if f"@admin_sekolah_bp.route({path}" in b)
            assert "_matrix_school()" in block or "_school_id()" in block, (
                f"{path} does not read the school from the session")
            assert 'form.get("school_id")' not in block, f"{path} trusts a school id from the form"

    def test_the_upload_checks_the_signature_and_never_writes_the_file(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        block = next(b for b in _route_blocks(source)
                     if '@admin_sekolah_bp.route("/invigilation/matrix/upload"' in b)
        assert "inv_matrix.is_xlsx(data)" in block, "the upload trusts the extension"
        assert ".save(" not in block and "open(" not in block, (
            "the uploaded workbook is written somewhere instead of parsed in memory")

    def test_the_apply_door_re_runs_the_rows_through_the_service(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        block = next(b for b in _route_blocks(source)
                     if '@admin_sekolah_bp.route("/invigilation/matrix/apply"' in b)
        assert "inv_matrix.apply_rows(" in block, (
            "the apply door writes by hand and skips the service's rules")

    def test_the_page_links_the_importer_the_template_and_the_reasons(self):
        page = PAGE.read_text(encoding="utf-8")
        assert "_invigilation_reasons.html" in page, "the page cannot say why a write was refused"
        assert "/admin-sekolah/invigilation/matrix/template.xlsx" in page
        assert "/admin-sekolah/invigilation/matrix/upload" in page
        assert "/admin-sekolah/invigilation/matrix/apply" in page

    def test_the_reasons_partial_explains_every_matrix_refusal(self):
        partial = REASONS.read_text(encoding="utf-8")
        missing = [key for key in im.REFUSALS
                   if not re.search(rf"'{key}':\s*\[\s*'[^']+',\s*'[^']+',?\s*\]", partial)]
        assert not missing, f"a refusal renders as an empty alert: {missing}"

    def test_the_nav_and_the_schedule_page_reach_the_matrix(self):
        assert "/admin-sekolah/invigilation/matrix" in PAGE.read_text(encoding="utf-8")
        schedule_page = (ROOT / "app" / "templates" / "principal" / "invigilation.html").read_text(
            encoding="utf-8")
        assert "/admin-sekolah/invigilation/matrix" in schedule_page, (
            "the schedule page does not link the matrix, so an operator has to know the URL")


# ── 10. seeding rooms and sessions from the school's own classes ────────────


def _seed_tables() -> dict:
    """A school with two classes of two and one pupils, and nothing seeded yet."""
    return {
        "classes": [
            {"id": "c1", "school_id": SCHOOL, "name": "7A", "grade_level": "7"},
            {"id": "c2", "school_id": SCHOOL, "name": "7B", "grade_level": "7"},
            {"id": "c9", "school_id": OTHER, "name": "9Z", "grade_level": "9"},
        ],
        "profiles": [
            {"id": "p1", "school_id": SCHOOL, "role": "murid", "class_id": "c1"},
            {"id": "p2", "school_id": SCHOOL, "role": "murid", "class_id": "c1"},
            {"id": "p3", "school_id": SCHOOL, "role": "murid", "class_id": "c2"},
            {"id": "p9", "school_id": OTHER, "role": "murid", "class_id": "c9"},
        ],
        "exam_period": [],
        "exam_room": [],
    }


class TestSeedingFromClasses:
    def test_class_rooms_counts_the_pupils_of_each_class(self):
        rooms = im.class_rooms(_DB(_seed_tables()), SCHOOL)
        by_name = {c["name"]: c for c in rooms}
        assert by_name["7A"]["pupil_count"] == 2
        assert by_name["7B"]["pupil_count"] == 1
        assert "9Z" not in by_name, "another school's class leaked into the seed"

    def test_seeding_makes_one_room_per_class_with_its_pupil_count(self):
        db = _DB(_seed_tables())
        out = im.seed_from_classes(db, SCHOOL)
        assert out["ok"] and out["rooms"] == 2, out
        assert {r["name"] for r in db.tables["exam_room"]} == {"7A", "7B"}
        assert {r["capacity"] for r in db.tables["exam_room"]} == {2, 1}
        assert all(r["school_id"] == SCHOOL for r in db.tables["exam_room"])

    def test_seeding_makes_three_sessions(self):
        db = _DB(_seed_tables())
        out = im.seed_from_classes(db, SCHOOL)
        assert out["periods"] == 3, out
        assert {p["name"] for p in db.tables["exam_period"]} == {"Sesi 1", "Sesi 2", "Sesi 3"}

    def test_seeding_twice_changes_nothing(self):
        db = _DB(_seed_tables())
        im.seed_from_classes(db, SCHOOL)
        again = im.seed_from_classes(db, SCHOOL)
        assert again["rooms"] == 0 and again["periods"] == 0, again
        assert len(db.tables["exam_room"]) == 2
        assert len(db.tables["exam_period"]) == 3

    def test_seeding_without_classes_is_refused_with_a_reason(self):
        db = _DB({**_seed_tables(), "classes": [], "profiles": []})
        out = im.seed_from_classes(db, SCHOOL)
        assert out["reason"] == "no_classes", out
        assert db.tables["exam_room"] == []

    def test_seeding_leaves_another_schools_rooms_alone(self):
        db = _DB({**_seed_tables(),
                  "exam_room": [{"id": "r9", "school_id": OTHER, "name": "Ruang 1",
                                 "capacity": 9, "is_active": True}]})
        im.seed_from_classes(db, SCHOOL)
        other = [r for r in db.tables["exam_room"] if r["school_id"] == OTHER]
        assert len(other) == 1 and other[0]["id"] == "r9"


# ── 11. the click grid: one teacher x one room, per session ─────────────────

class TestClickingACell:
    def test_clicking_an_empty_cell_assigns_the_teacher(self):
        db = _DB(_tables())
        out = im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                          room_id="r1", teacher_id="t1", actor_id="a1")
        assert out["ok"] and out["action"] == "set", out
        assert [d["teacher_id"] for d in db.tables["invigilation_duty"]] == ["t1"]

    def test_clicking_the_same_cell_again_clears_it(self):
        db = _DB(_tables())
        im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                    room_id="r1", teacher_id="t1")
        out = im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                          room_id="r1", teacher_id="t1")
        assert out["ok"] and out["action"] == "cleared", out
        assert db.tables["invigilation_duty"] == []

    def test_clicking_a_room_adding_a_second_teacher(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", room="r1", teacher="t1"))
        out = im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                          room_id="r1", teacher_id="t2")
        assert out["ok"] and out["action"] == "set", out
        assert sorted(d["teacher_id"] for d in db.tables["invigilation_duty"]) == ["t1", "t2"]

    def test_clicking_adds_the_teacher_to_their_second_room(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", room="r1", teacher="t1"))
        im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                    room_id="r2", teacher_id="t1")
        pairs = sorted((d["room_id"], d["teacher_id"]) for d in db.tables["invigilation_duty"])
        assert pairs == [("r1", "t1"), ("r2", "t1")], pairs

    def test_a_click_that_would_cross_the_room_cap_is_refused(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"] = [
            _duty("d1", room="r1", teacher="t1"),
            _duty("d2", room="r1", teacher="t2"),
        ]
        db.tables["teachers"].append({"id": "t3", "school_id": SCHOOL,
                                      "profiles": {"full_name": "Bu Tiga"}})
        out = im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                          room_id="r1", teacher_id="t3")
        assert out["reason"] == "room_full", out
        assert len(db.tables["invigilation_duty"]) == 2

    def test_a_click_that_would_cross_the_teacher_cap_is_refused(self):
        db = _DB(_tables())
        db.tables["exam_room"].append({"id": "r3", "school_id": SCHOOL, "name": "Ruang 3",
                                       "capacity": 20, "is_active": True})
        db.tables["invigilation_duty"] = [
            _duty("d1", room="r1", teacher="t1"),
            _duty("d2", room="r2", teacher="t1"),
        ]
        out = im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                          room_id="r3", teacher_id="t1")
        assert out["reason"] == "teacher_full", out

    def test_a_saved_click_reports_the_seat_counts_back(self):
        db = _DB(_tables())
        out = im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                          room_id="r1", teacher_id="t1")
        assert out["room_counts"]["r1"] == 1 and out["teacher_counts"]["t1"] == 1, out
        assert out["max_per_room"] == 2 and out["max_per_teacher"] == 2

    def test_a_click_never_touches_another_period(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", period="p2", room="r1", teacher="t1"))
        im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                    room_id="r1", teacher_id="t1")
        kept = [d for d in db.tables["invigilation_duty"] if d["period_id"] == "p2"]
        assert len(kept) == 1, "a click in one session changed another session"

    def test_a_foreign_room_is_refused_and_writes_nothing(self):
        db = _DB(_tables())
        out = im.set_cell(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                          room_id="r9", teacher_id="t1")
        assert out["reason"] == "room_not_in_school", out
        assert db.tables["invigilation_duty"] == []


# ── 12. auto-fill: every empty room of a session, one free teacher each ──────

class TestAutoFill:
    def test_auto_fill_puts_a_free_teacher_in_every_empty_room(self):
        db = _DB(_tables())
        out = im.auto_fill(db, SCHOOL, exam_date="2026-10-01", period_id="p1")
        assert out["ok"] and out["applied"] == 2, out
        assert {d["room_id"] for d in db.tables["invigilation_duty"]} == {"r1", "r2"}
        assert {d["teacher_id"] for d in db.tables["invigilation_duty"]} == {"t1", "t2"}

    def test_auto_fill_leaves_a_filled_room_alone(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", room="r1", teacher="t1"))
        out = im.auto_fill(db, SCHOOL, exam_date="2026-10-01", period_id="p1")
        assert out["applied"] == 1, out
        occupied = {d["room_id"]: d["teacher_id"] for d in db.tables["invigilation_duty"]}
        assert occupied["r1"] == "t1"

    def test_auto_fill_never_puts_one_teacher_in_two_rooms(self):
        db = _DB(_tables())
        im.auto_fill(db, SCHOOL, exam_date="2026-10-01", period_id="p1")
        teachers = [d["teacher_id"] for d in db.tables["invigilation_duty"]]
        assert len(teachers) == len(set(teachers))

    def test_auto_fill_refuses_another_schools_period(self):
        db = _DB(_tables())
        out = im.auto_fill(db, SCHOOL, exam_date="2026-10-01", period_id="p9")
        assert out["reason"] == "period_not_in_school", out


# ── 13. the new doors and the page's new controls ───────────────────────────

class TestTheNewDoors:
    def test_the_new_routes_are_guarded_and_scoped_to_the_session(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        for path in ('"/invigilation/matrix/seed"', '"/invigilation/matrix/cell"',
                     '"/invigilation/matrix/auto-fill"'):
            assert path in source, f"{path} is missing"
            block = next(b for b in _route_blocks(source)
                         if f"@admin_sekolah_bp.route({path}" in b)
            assert "@admin_sekolah_required" in block, f"{path} is unguarded"
            assert "_matrix_school()" in block or "_school_id()" in block, (
                f"{path} does not read the school from the session")
            assert 'form.get("school_id")' not in block, f"{path} trusts a school id from the form"

    def test_the_page_offers_the_seed_autofill_and_click_cells(self):
        page = PAGE.read_text(encoding="utf-8")
        assert "/admin-sekolah/invigilation/matrix/seed" in page, "no 'build from classes' button"
        assert "/admin-sekolah/invigilation/matrix/auto-fill" in page, "no auto-fill button"
        assert "/admin-sekolah/invigilation/matrix/cell" in page, "no click-to-assign cell"

    def test_the_cell_form_saves_without_reloading(self):
        page = PAGE.read_text(encoding="utf-8")
        # The form is posted by fetch with the XHR header, the button is repainted
        # from the JSON, and the seat counters are read back — so no reload.
        assert "class=\"matrix-cell\"" in page
        assert "X-Requested-With" in page and "XMLHttpRequest" in page
        assert "preventDefault()" in page
        assert "paintSeats" in page
        assert "data-room-seat" in page and "data-teacher-seat" in page
        assert "matrix-min-summary" in page and "data-empty-rooms" in page

    def test_the_cell_route_answers_json_for_the_no_reload_path(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        block = next(b for b in _route_blocks(source)
                     if '@admin_sekolah_bp.route("/invigilation/matrix/cell"' in b)
        assert "X-Requested-With" in block and "jsonify" in block
        assert '"room_counts"' in block and '"max_per_room"' in block


# ── 14. the range: one teacher across a run of rooms in one act ─────────────

class TestSetRange:
    """Shift-click hands the service one intent and a run of rooms.

    The point is fewer round trips for the tedious part — but a range is not a
    licence to break the two caps, so every room in the run still goes through the
    very same :func:`assign` a single click uses. What the range may not do is land
    a third room for a teacher: the run fills what it can and names the room that
    was refused.
    """

    def _three_rooms(self):
        tables = _tables()
        tables["exam_room"].append({"id": "r3", "school_id": SCHOOL, "name": "Ruang 3",
                                    "capacity": 20, "is_active": True})
        return tables

    def test_a_range_sets_the_teacher_into_every_room_of_the_run(self):
        db = _DB(_tables())
        out = im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                           teacher_id="t1", room_ids=["r1", "r2"], mode="set",
                           actor_id="a1")
        assert out["ok"] and out["applied"] == 2 and out["refused"] == 0, out
        pairs = sorted((d["room_id"], d["teacher_id"])
                       for d in db.tables["invigilation_duty"])
        assert pairs == [("r1", "t1"), ("r2", "t1")], pairs

    def test_a_range_clears_the_teacher_from_every_room_of_the_run(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"] = [
            _duty("d1", room="r1", teacher="t1"),
            _duty("d2", room="r2", teacher="t1"),
        ]
        out = im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                           teacher_id="t1", room_ids=["r1", "r2"], mode="clear")
        assert out["ok"] and out["applied"] == 2, out
        assert db.tables["invigilation_duty"] == []

    def test_a_run_past_the_teacher_cap_lands_what_it_can_and_names_the_refusal(self):
        db = _DB(self._three_rooms())
        out = im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                           teacher_id="t1", room_ids=["r1", "r2", "r3"], mode="set")
        assert out["ok"] and out["applied"] == 2 and out["refused"] == 1, out
        assert out["reason"] == "teacher_full", out
        rooms = {d["room_id"] for d in db.tables["invigilation_duty"]}
        assert rooms == {"r1", "r2"}, "the run crossed the cap"

    def test_a_range_set_is_idempotent_where_the_teacher_is_already_seated(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", room="r1", teacher="t1"))
        out = im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                           teacher_id="t1", room_ids=["r1", "r2"], mode="set")
        assert out["ok"] and out["applied"] == 2, out
        assert len(db.tables["invigilation_duty"]) == 2, "the run wrote a duplicate cell"

    def test_a_range_refuses_a_foreign_room_without_writing_to_it(self):
        db = _DB(_tables())
        out = im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                           teacher_id="t1", room_ids=["r1", "r9"], mode="set")
        assert out["refused"] == 1 and out["reason"] == "room_not_in_school", out
        assert [d["room_id"] for d in db.tables["invigilation_duty"]] == ["r1"], (
            "the run wrote into a room that is not this school's")

    def test_a_range_never_touches_another_session(self):
        db = _DB(_tables())
        db.tables["invigilation_duty"].append(_duty("d1", period="p2", room="r1", teacher="t1"))
        im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                     teacher_id="t1", room_ids=["r1"], mode="clear")
        assert [d["period_id"] for d in db.tables["invigilation_duty"]] == ["p2"], (
            "clearing a range in one session emptied another session")

    def test_a_range_reports_the_seat_counts_back_for_the_repaint(self):
        db = _DB(_tables())
        out = im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                           teacher_id="t1", room_ids=["r1", "r2"], mode="set")
        assert out["room_counts"]["r1"] == 1 and out["room_counts"]["r2"] == 1, out
        assert out["teacher_counts"]["t1"] == 2, out
        assert out["max_per_room"] == im.MAX_PER_ROOM
        assert out["max_per_teacher"] == im.MAX_PER_TEACHER

    def test_a_range_refuses_an_unknown_mode(self):
        db = _DB(_tables())
        out = im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                           teacher_id="t1", room_ids=["r1"], mode="toggle")
        assert not out["ok"] and db.tables["invigilation_duty"] == []

    def test_a_range_refuses_another_schools_teacher(self):
        db = _DB(_tables())
        out = im.set_cells(db, SCHOOL, exam_date="2026-10-01", period_id="p1",
                           teacher_id="t9", room_ids=["r1"], mode="set")
        assert out["reason"] == "teacher_not_in_school", out
        assert db.tables["invigilation_duty"] == []


class TestTheRangeDoor:
    def test_the_batch_route_is_guarded_and_scoped_to_the_session(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        path = '"/invigilation/matrix/cells"'
        assert path in source, "the range door is missing"
        block = next(b for b in _route_blocks(source)
                     if f"@admin_sekolah_bp.route({path}" in b)
        assert "@admin_sekolah_required" in block, f"{path} is unguarded"
        assert "_matrix_school()" in block or "_school_id()" in block
        assert 'form.get("school_id")' not in block
        assert 'getlist("room_id")' in block, "the door takes one room, not a run"

    def test_the_batch_route_answers_json_for_the_no_reload_path(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        block = next(b for b in _route_blocks(source)
                     if '@admin_sekolah_bp.route("/invigilation/matrix/cells"' in b)
        assert "X-Requested-With" in block and "jsonify" in block
        assert '"results"' in block
        assert "inv_matrix.set_cells(" in block

    def test_the_page_reaches_the_batch_door_and_teaches_shift_click(self):
        page = PAGE.read_text(encoding="utf-8")
        assert "/admin-sekolah/invigilation/matrix/cells" in page, (
            "the page cannot reach the range door")
        assert "shiftKey" in page, "no shift-click range selection in the grid's script"
        assert "paintCell" in page, "the range repaint is not factored out of the single click"
        # The interaction must be discoverable, not a hidden keystroke.
        assert re.search(r"shift[^<]{0,80}(klik|click)", page, re.I), (
            "the page never tells the operator that shift-click fills a run")
