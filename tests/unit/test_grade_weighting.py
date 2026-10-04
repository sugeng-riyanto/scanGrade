"""A final mark is a policy, and these are the numbers that prove the policy ran.

The arithmetic here is checked against hand-worked examples on purpose. A test
that only asserts "no exception" would pass while the weights multiplied the wrong
way round — a mark 30% too high still renders fine.

The other half is the contract for a school that configured nothing: the simple
mean must come back, unchanged, or every page built before this feature breaks for
the schools that have not opened the settings.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import grade_weighting as gw

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "057_grade_weighting.sql"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    """A fake that really filters, so a missing school scope is a real miss."""

    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters = []
        self.op = "select"
        self.payload = None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, str(val)))
        return self

    def in_(self, col, values):
        self.filters.append(("in", col, {str(v) for v in values}))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _match(self, row):
        for op, col, val in self.filters:
            got = str(row.get(col))
            if op == "eq" and got != val:
                return False
            if op == "in" and got not in val:
                return False
        return True

    def execute(self):
        self.store.log.append((self.op, self.table, self.payload, tuple(self.filters)))
        rows = self.store.tables.setdefault(self.table, [])
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for it in items:
                row = dict(it)
                row.setdefault("id", f"r{len(rows) + 1}")
                rows.append(row)
                out.append(row)
            return _Resp(out)
        if self.op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.payload)
            return _Resp(hit)
        return _Resp([dict(r) for r in rows if self._match(r)])


class _Sb:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.log = []

    def table(self, name):
        return _Q(self, name)


SCHOOL = "sch-1"
SUBJECT = "sub-1"
YEAR = "year-1"
TUGAS, UTS, UAS = "comp-tugas", "comp-uts", "comp-uas"


# ── the arithmetic, by hand ──────────────────────────────────────────────────

class TestTheWeightedMark:
    def test_every_component_filled_multiplies_and_sums(self):
        """Tugas 85 (30%) + UTS 70 (30%) + UAS 90 (40%) = 82.5, worked by hand."""
        rows = [(TUGAS, 85), (UTS, 70), (UAS, 90)]
        out = gw.compute(rows, {TUGAS: 30, UTS: 30, UAS: 40})
        assert out["mode"] == "weighted"
        assert out["final"] == 82.5, out
        assert out["total"] == 100
        assert out["untagged"] == 0

    def test_a_component_averages_its_own_papers_first(self):
        """Two Tugas marks (80, 90) average to 85 before the weight is applied."""
        rows = [(TUGAS, 80), (TUGAS, 90), (UTS, 70), (UAS, 90)]
        out = gw.compute(rows, {TUGAS: 30, UTS: 30, UAS: 40})
        # 85*.30 + 70*.30 + 90*.40 = 25.5 + 21.0 + 36.0 = 82.5
        assert out["final"] == 82.5
        tugas = next(d for d in out["detail"] if d["component_id"] == TUGAS)
        assert tugas["average"] == 85.0 and tugas["count"] == 2

    def test_a_missing_component_counts_as_zero_not_renormalised(self):
        """UAS has 40% and no score: it is 0, so the mark is 46.5, not 85.
        Renormalising would silently raise a mark for work never done."""
        rows = [(TUGAS, 85), (UTS, 70)]
        out = gw.compute(rows, {TUGAS: 30, UTS: 30, UAS: 40})
        # 85*.30 + 70*.30 + 0*.40 = 25.5 + 21.0 = 46.5
        assert out["final"] == 46.5
        uas = next(d for d in out["detail"] if d["component_id"] == UAS)
        assert uas["average"] is None and uas["weight"] == 40

    def test_weights_are_not_interchangeable(self):
        """Swapping the weights changes the mark — proves they are applied, not ignored."""
        rows = [(TUGAS, 100), (UTS, 0), (UAS, 0)]
        assert gw.compute(rows, {TUGAS: 100, UTS: 0, UAS: 0})["final"] == 100.0
        assert gw.compute(rows, {TUGAS: 10, UTS: 45, UAS: 45})["final"] == 10.0

    def test_an_uncategorised_paper_is_ignored_and_counted(self):
        """A scored paper with no component cannot be placed in a weighted mark."""
        rows = [(None, 100), (TUGAS, 80)]
        out = gw.compute(rows, {TUGAS: 100})
        assert out["final"] == 80.0
        assert out["untagged"] == 1


class TestTheFallbackForASchoolThatConfiguredNothing:
    def test_no_weights_means_the_simple_mean(self):
        rows = [(TUGAS, 80), (UTS, 90), (UAS, 70), (None, 100)]
        out = gw.compute(rows, {})
        assert out["mode"] == "simple"
        assert out["final"] == 85.0            # (80+90+70+100)/4
        assert out["detail"] == []

    def test_unscored_sittings_are_not_zeroes_in_the_simple_mean(self):
        rows = [(TUGAS, 80), (UTS, None), (UAS, 90)]
        out = gw.compute(rows, {})
        assert out["final"] == 85.0            # None skipped, not read as 0

    def test_nothing_scored_reads_as_no_mark_not_zero(self):
        assert gw.compute([], {})["final"] is None
        assert gw.compute([(TUGAS, None)], {})["final"] is None

    def test_the_mode_is_always_named(self):
        assert gw.compute([(TUGAS, 80)], {})["mode"] == "simple"
        assert gw.compute([(TUGAS, 80)], {TUGAS: 100})["mode"] == "weighted"


# ── the write guards ─────────────────────────────────────────────────────────

class TestSavingTheWeights:
    def _sb(self):
        return _Sb(
            subjects=[{"id": SUBJECT, "school_id": SCHOOL}],
            grade_component_type=[
                {"id": TUGAS, "school_id": SCHOOL, "name": "Tugas", "is_active": True},
                {"id": UTS, "school_id": SCHOOL, "name": "UTS", "is_active": True},
                {"id": UAS, "school_id": SCHOOL, "name": "UAS", "is_active": True},
            ],
            grade_weight_config=[],
        )

    def test_a_total_of_100_is_accepted(self):
        sb = self._sb()
        ok, res = gw.save_config(sb, SCHOOL, SUBJECT, YEAR,
                                 {TUGAS: 30, UTS: 30, UAS: 40})
        assert ok and res["total"] == 100

    def test_a_total_that_is_not_100_is_refused_before_any_write(self):
        sb = self._sb()
        ok, res = gw.save_config(sb, SCHOOL, SUBJECT, YEAR,
                                 {TUGAS: 30, UTS: 30, UAS: 30})
        assert not ok and res["status"] == 400 and res["total"] == 90
        assert not [entry for entry in sb.log if entry[0] in ("insert", "update")]

    def test_a_component_from_another_school_is_refused(self):
        sb = self._sb()
        ok, res = gw.save_config(sb, SCHOOL, SUBJECT, YEAR,
                                 {TUGAS: 50, "someone-elses": 50})
        assert not ok and res["status"] == 403

    def test_a_value_above_100_is_refused(self):
        sb = self._sb()
        ok, res = gw.save_config(sb, SCHOOL, SUBJECT, YEAR, {TUGAS: 120})
        assert not ok and res["status"] == 400

    def test_an_empty_set_clears_the_subject_back_to_the_mean(self):
        """Clearing is deactivation, not deletion — a mark's history survives."""
        sb = self._sb()
        sb.tables["grade_weight_config"] = [
            {"id": "w1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "component_id": TUGAS, "weight_percent": 100,
             "is_active": True},
        ]
        ok, res = gw.save_config(sb, SCHOOL, SUBJECT, YEAR, {})
        assert ok and res["cleared"] is True
        updates = [p for op, t, p, _f in sb.log if op == "update" and t == "grade_weight_config"]
        assert any(p.get("is_active") is False for p in updates), "clear must deactivate"

    def test_re_saving_reuses_the_row(self):
        """A second save updates the pair's row rather than piling up a duplicate."""
        sb = self._sb()
        sb.tables["grade_weight_config"] = [
            {"id": "w1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "component_id": TUGAS, "weight_percent": 100,
             "is_active": True},
        ]
        ok, _res = gw.save_config(sb, SCHOOL, SUBJECT, YEAR, {TUGAS: 40, UTS: 60})
        inserts = [p for op, t, p, _f in sb.log if op == "insert" and t == "grade_weight_config"]
        rows = [r for chunk in inserts for r in (chunk if isinstance(chunk, list) else [chunk])]
        assert all(str(r["component_id"]) != TUGAS for r in rows), "Tugas was inserted twice"


class TestReadingAConfig:
    def test_a_missing_year_is_not_configured(self):
        sb = _Sb(grade_weight_config=[{"school_id": SCHOOL, "subject_id": SUBJECT,
                                       "school_year_id": YEAR, "component_id": TUGAS,
                                       "weight_percent": 100, "is_active": True}])
        assert gw.config_for(sb, SCHOOL, SUBJECT, None) == {}

    def test_only_active_rows_are_read(self):
        sb = _Sb(grade_weight_config=[
            {"school_id": SCHOOL, "subject_id": SUBJECT, "school_year_id": YEAR,
             "component_id": TUGAS, "weight_percent": 60, "is_active": True},
            {"school_id": SCHOOL, "subject_id": SUBJECT, "school_year_id": YEAR,
             "component_id": UTS, "weight_percent": 40, "is_active": False},
        ])
        assert gw.config_for(sb, SCHOOL, SUBJECT, YEAR) == {TUGAS: 60}


# ── the schema this all rests on ─────────────────────────────────────────────

class TestTheSchema:
    def test_the_migration_is_additive_and_idempotent(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS public.grade_component_type" in sql
        assert "CREATE TABLE IF NOT EXISTS public.grade_weight_config" in sql
        assert "ADD COLUMN IF NOT EXISTS grade_component_type_id" in sql
        for bad in ("DROP TABLE", "DROP COLUMN", "DELETE FROM"):
            assert bad not in sql.upper(), f"the migration is destructive: {bad}"

    def test_the_component_column_is_nullable(self):
        """Old exams must survive: the tag is optional, never a not-null backfill."""
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "grade_component_type_id UUID" in sql
        assert "grade_component_type_id UUID NOT NULL" not in sql.upper()

    def test_the_range_and_uniqueness_are_in_the_database(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "weight_percent BETWEEN 0 AND 100" in sql
        assert "idx_grade_weight_unique" in sql and "(subject_id, school_year_id, component_id)" in sql

    def test_every_table_is_school_readable_and_admin_writable(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert sql.count("ENABLE ROW LEVEL SECURITY") == 2
        for policy in ("grade_component_school_read", "grade_component_admin_write",
                       "grade_weight_school_read", "grade_weight_admin_write"):
            assert f'CREATE POLICY "{policy}"' in sql, f"{policy} is missing"
        # Two admin policies, each USING + WITH CHECK; every policy names the school.
        assert sql.count("public._is_role('admin_sekolah')") == 4
        assert sql.count("public._user_school_id()") == 6
