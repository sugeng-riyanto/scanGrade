"""A pupil's year-by-year membership, and the words its outcome may use.

`students.class_id` is a single mutable pointer. Promotion overwrites it (in two
tables), so after a promotion nothing records the class a child sat in last year.
`student_enrollment` (migration 046) is that record, and this file pins the rules
that decide what a row says:

* the five statuses, kept in step with the database CHECK;
* a school's last grade defaults to `lulus`, not `naik`;
* one membership per pupil per year, written through a conflict target that
  actually exists;
* history is oldest-first and gaps stay gaps.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from app.services import enrollment

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "046_student_enrollment.sql"


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.upserted = None
        self.conflict = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self

    def upsert(self, payload, on_conflict=None):
        self.upserted = payload
        self.conflict = on_conflict
        return self

    def execute(self):
        if self.upserted is not None:
            return SimpleNamespace(data=[dict(self.upserted, id="e-1")])
        return SimpleNamespace(data=self.rows)


class _Sb:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.query = _Query(self.rows)

    def table(self, name):
        assert name in ("student_enrollment",), name
        return self.query


# ── the vocabulary ───────────────────────────────────────────────────────────

def test_the_service_statuses_match_the_database_check():
    sql = MIGRATION.read_text(encoding="utf-8")
    check = re.search(r"CHECK \(status IN \(([^)]*)\)\)", sql).group(1)
    in_sql = set(re.findall(r"'([a-z_]+)'", check))
    assert in_sql == set(enrollment.STATUSES), (
        f"migration allows {sorted(in_sql)}, service knows {sorted(enrollment.STATUSES)}")


def test_an_unknown_status_is_not_accepted_as_one():
    assert not enrollment.is_status("naik kelas")
    assert not enrollment.is_status("NAIK")
    # ...but a caller's spelling is normalised, not waved through
    assert enrollment.normalise_status(" LULUS ") == "lulus"
    assert enrollment.normalise_status("nonsense") == "aktif"
    assert enrollment.normalise_status("nonsense", default="pindah") == "pindah"


# ── the last grade graduates ─────────────────────────────────────────────────

def test_the_final_grade_defaults_to_lulus_not_naik():
    levels = ["7", "8", "9"]
    assert enrollment.default_outcome("9", levels) == "lulus"
    assert enrollment.default_outcome("8", levels) == "naik"


def test_roman_grades_are_ordered_too():
    levels = ["VII", "VIII", "IX"]
    assert enrollment.grade_order(levels) == ["VII", "VIII", "IX"]
    assert enrollment.default_outcome("IX", levels) == "lulus"
    assert enrollment.default_outcome("VII", levels) == "naik"


def test_a_mixed_label_set_orders_numerically():
    """A school mid-migration from VII to 7 must not read one as lower."""
    assert enrollment.grade_order(["10", "9", "XI", "7"]) == ["7", "9", "10", "XI"]


def test_grade_order_deduplicates():
    """One grade label per LEVEL, not per class row.

    A school has many classes at one grade (8A, 8B, 8C). Keeping the duplicates
    makes "the next grade after 8" resolve to 8, so every pupil would be promoted
    into their own year — the defect this file's sibling found.
    """
    assert enrollment.grade_order(["8", "8", "9", "9"]) == ["8", "9"]


def test_no_known_levels_gradautes_nobody():
    assert enrollment.default_outcome("9", []) == "naik"


# ── writing a row ────────────────────────────────────────────────────────────

def test_record_names_the_conflict_target_that_exists():
    """`(student_id, school_year_id)` is 046's partial unique index; targeting the
    primary key would have nothing to collide with and raise 23505 instead."""
    sb = _Sb()
    enrollment.record(sb, "sc-1", "st-1", "yr-1", "c-1", status="naik")
    assert sb.query.conflict == "student_id,school_year_id"
    assert sb.query.upserted["status"] == "naik"
    assert sb.query.upserted["class_id"] == "c-1"


def test_record_normalises_the_status_before_writing():
    sb = _Sb()
    enrollment.record(sb, "sc-1", "st-1", "yr-1", "c-1", status=" Pindah ")
    assert sb.query.upserted["status"] == "pindah"


# ── reading a history ────────────────────────────────────────────────────────

def test_history_is_ordered_oldest_first_by_year_start():
    rows = [
        {"id": "2", "student_id": "st-1",
         "school_years": {"name": "2026/2027", "start_date": "2026-07-01"}},
        {"id": "1", "student_id": "st-1",
         "school_years": {"name": "2025/2026", "start_date": "2025-07-01"}},
    ]
    ordered = enrollment.history(_Sb(rows), "st-1")
    assert [r["id"] for r in ordered] == ["1", "2"]


def test_current_class_reads_the_row_for_the_year():
    sb = _Sb([{"class_id": "c-9", "student_id": "st-1", "school_year_id": "yr-1"}])
    assert enrollment.current_class_id(sb, "st-1", "yr-1") == "c-9"


def test_current_class_is_none_rather_than_a_guess():
    assert enrollment.current_class_id(_Sb([{"class_id": "c-9"}]), "st-1", None) is None


def test_a_missing_year_stays_a_gap_not_a_zero():
    series = enrollment.progress_by_year([
        {"school_years": {"name": "2025/2026"}, "subject": None},
        {"school_years": {"name": "2026/2027"}, "subject": 80},
    ])
    assert series[0]["has_data"] is False
    assert series[0]["value"] is None
    assert series[1]["has_data"] is True


# ── the wiring ───────────────────────────────────────────────────────────────

ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"


def test_the_promote_route_records_membership():
    """Promotion used to overwrite the class pointer and leave no trace."""
    src = ROUTES.read_text(encoding="utf-8")
    promote = src.split("def promote(")[1].split("\n# \u2500\u2500\u2500")[0]
    assert "enrollment.record(" in promote, (
        "promotion still erases the class a pupil was in instead of recording it")


def test_the_outcome_defaults_by_rule_not_by_a_literal():
    src = ROUTES.read_text(encoding="utf-8")
    promote = src.split("def promote(")[1].split("\n# \u2500\u2500\u2500")[0]
    assert "default_outcome(" in promote, (
        "the last grade would advance instead of graduate if the default is a literal")


def test_the_promote_form_offers_the_four_outcomes():
    page = (ROOT / "app" / "templates" / "admin_sekolah" / "promote.html").read_text(encoding="utf-8")
    assert 'name="outcome"' in page
    for status in ("naik", "tinggal_kelas", "lulus", "pindah"):
        assert 'value="%s"' % status in page, status


# ── the migration ────────────────────────────────────────────────────────────

def test_the_migration_is_additive():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql), "the migration is destructive"
    assert not re.search(r"DELETE\s+FROM", sql), "the migration deletes rows"
    assert "CREATE TABLE IF NOT EXISTS student_enrollment" in sql
    assert "ADD COLUMN IF NOT EXISTS school_year_id" in sql


def test_the_unique_index_is_per_pupil_per_year():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "UNIQUE" in sql.upper()
    assert "(student_id, school_year_id)" in sql, (
        "without this, a repeated promotion duplicates the year instead of updating it")


def test_every_policy_names_its_caller():
    """A policy without `TO` applies to PUBLIC, including `anon`."""
    sql = MIGRATION.read_text(encoding="utf-8")
    blocks = re.findall(r"CREATE POLICY.*?;", sql, re.S)
    assert blocks, "no policies at all"
    for block in blocks:
        assert " TO authenticated" in block, block[:80]
