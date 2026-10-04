"""Every exam is tagged with the assessment period it belongs to, automatically.

`assessment_periods` names a date range and `exams.exam_type` carries the same
vocabulary, but nothing joined the two: `exams` had no column pointing at a period,
so a school could not group its results by UTS/UAS/Try Out — the period a paper
belongs to was implied by its dates and lost the moment the dates were edited.

This adds the join the grouping needs:

* `exams.assessment_period_id` — an FK to the period, so the tag cannot name a
  period that does not exist;
* an automatic tag at both write doors, so a paper is filed the moment it is made
  (or edited) rather than by a human remembering;
* an idempotent backfill for the papers that already exist;
* `compare_by_period`, the read that turns the tag into a comparison across a year.

The tag is the period the window sits in. At a write door that is the running
period, because a window outside it is refused (the calendar gate) — so there is
no third case to guess at, and a school with no running period writes NULL.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEACHER = ROOT / "app" / "routes" / "teacher.py"
SERVICE = ROOT / "app" / "services" / "assessment_periods.py"
SCOPE = ROOT / "app" / "services" / "analysis_scope.py"
ANALYTICS = ROOT / "app" / "templates" / "teacher" / "analytics.html"
MIGRATION = ROOT / "supabase" / "migrations" / "055_exam_assessment_period.sql"


# ── a fake Supabase / a period row ───────────────────────────────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.filters, self.mode, self.payload = [], "select", None

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("IN " + column, set(map(str, values))))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def _matches(self, row):
        for key, value in self.filters:
            if key.startswith("IN "):
                if str(row.get(key[3:])) not in value:
                    return False
            elif str(row.get(key)) != str(value):
                return False
        return True

    def execute(self):
        self.store.calls.append(self.table)
        rows = self.store.rows.get(self.table, [])
        if self.mode == "insert":
            self.store.inserted.append((self.table, self.payload))
            return _Resp([dict(self.payload, id="new-1")])
        return _Resp([r for r in rows if self._matches(r)])


class FakeSupabase:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.inserted = []
        self.calls = []

    def table(self, name):
        return _Query(self, name)


def _period(**over):
    row = {"id": "p1", "school_id": "s1", "kind": "mid_semester",
           "name": "UTS Ganjil", "start_date": "2026-09-01",
           "end_date": "2026-09-10", "is_active": True}
    row.update(over)
    return row


# ── the tag a paper gets ─────────────────────────────────────────────────────

class TestTheTagForAWindow:
    def test_a_window_inside_a_period_is_tagged_with_that_period(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [
            _period(id="p-old", name="Lama", start_date="2026-01-01",
                    end_date="2026-01-10", is_active=False),
            _period(id="p1")]})
        period = ap.period_for_exam(sb, "s1", "2026-09-03T02:00:00",
                                    "2026-09-04T02:00:00")
        assert period and period["id"] == "p1"

    def test_a_window_in_an_earlier_period_is_tagged_to_that_one(self):
        """A paper edited after its period ended is still that period's paper."""
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [
            _period(id="p-old", name="Lama", start_date="2026-01-01",
                    end_date="2026-01-10", is_active=False),
            _period(id="p1")]})
        period = ap.period_for_exam(sb, "s1", "2026-01-05T02:00:00",
                                    "2026-01-06T02:00:00")
        assert period and period["id"] == "p-old"

    def test_no_window_falls_back_to_the_running_period(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [_period()]})
        period = ap.period_for_exam(sb, "s1", None, None)
        assert period and period["id"] == "p1"

    def test_no_period_at_all_tags_nothing(self):
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": []})
        assert ap.period_for_exam(sb, "s1", "2026-09-03T02:00:00", None) is None

    def test_a_failed_read_tags_nothing_rather_than_raising(self):
        from app.services import assessment_periods as ap

        class Boom(FakeSupabase):
            def table(self, name):
                raise RuntimeError("down")

        assert ap.period_for_exam(Boom(), "s1", "2026-09-03T02:00:00", None) is None


# ── grouping results by period ───────────────────────────────────────────────

def _row(period_id, *, count, mean, pass_pct, lo=40.0, hi=90.0, title="X"):
    return {"id": "e-" + title, "title": title, "period_id": period_id,
            "count": count, "mean": mean, "pass_pct": pass_pct,
            "min": lo, "max": hi}


class TestTheComparisonAcrossAPeriod:
    def test_rows_are_grouped_by_their_period(self):
        from app.services import assessment_periods as ap

        periods = {"p1": _period(id="p1"), "p2": _period(
            id="p2", name="UAS", start_date="2026-12-01", end_date="2026-12-10")}
        groups = ap.compare_by_period(
            [_row("p1", count=10, mean=80.0, pass_pct=90.0),
             _row("p2", count=20, mean=60.0, pass_pct=50.0, title="Y")],
            periods)

        assert [g["period_id"] for g in groups] == ["p2", "p1"], (
            "groups must be ordered newest period first")
        p2 = groups[0]
        assert p2["name"] == "UAS" and p2["exams"] == 1 and p2["papers"] == 20
        assert p2["mean"] == 60.0 and p2["pass_rate"] == 50.0

    def test_aggregates_are_weighted_by_papers_not_by_exam(self):
        """Two exams of 10 and 90 papers must not average as (mean+mean)/2."""
        from app.services import assessment_periods as ap

        periods = {"p1": _period(id="p1")}
        groups = ap.compare_by_period(
            [_row("p1", count=10, mean=100.0, pass_pct=100.0),
             _row("p1", count=90, mean=50.0, pass_pct=0.0, title="Y")],
            periods)

        assert groups[0]["papers"] == 100
        assert groups[0]["mean"] == 55.0, "the mean ignored how many sat each paper"
        assert groups[0]["pass_rate"] == 10.0

    def test_the_best_and_worst_marks_are_the_extremes_across_the_period(self):
        from app.services import assessment_periods as ap

        periods = {"p1": _period(id="p1")}
        groups = ap.compare_by_period(
            [_row("p1", count=10, mean=80.0, pass_pct=90.0, lo=30.0, hi=95.0),
             _row("p1", count=10, mean=70.0, pass_pct=80.0, lo=40.0, hi=88.0, title="Y")],
            periods)
        assert groups[0]["best"] == 95.0 and groups[0]["worst"] == 30.0

    def test_untagged_rows_are_collected_not_dropped(self):
        from app.services import assessment_periods as ap

        groups = ap.compare_by_period(
            [_row(None, count=5, mean=70.0, pass_pct=60.0)],
            {})
        assert len(groups) == 1 and groups[0]["period_id"] is None
        assert groups[0]["exams"] == 1 and groups[0]["papers"] == 5

    def test_a_paper_nobody_sat_does_not_divide_by_zero(self):
        from app.services import assessment_periods as ap

        periods = {"p1": _period(id="p1")}
        groups = ap.compare_by_period(
            [_row("p1", count=0, mean=None, pass_pct=None)],
            periods)
        assert groups[0]["papers"] == 0
        assert groups[0]["mean"] is None and groups[0]["pass_rate"] is None

    def test_an_empty_report_groups_into_nothing(self):
        from app.services import assessment_periods as ap

        assert ap.compare_by_period([], {}) == []


class TestNamingPeriodsForAReport:
    def test_periods_are_read_by_id_across_schools(self):
        """A super admin's report spans schools, so a by-school read misses rows."""
        from app.services import assessment_periods as ap

        sb = FakeSupabase(rows={"assessment_periods": [
            _period(id="p1", school_id="s1"), _period(id="p2", school_id="s2")]})
        found = ap.periods_by_id(sb, ["p1", "p2"])
        assert set(found) == {"p1", "p2"}

    def test_reading_by_id_tolerates_empty_input(self):
        from app.services import assessment_periods as ap

        assert ap.periods_by_id(FakeSupabase(), []) == {}
        assert ap.periods_by_id(FakeSupabase(), [None, ""]) == {}


# ── the report carries the grouping ──────────────────────────────────────────

class TestTheReportCarriesThePeriod:
    def test_the_exam_columns_include_the_period(self):
        src = SCOPE.read_text(encoding="utf-8")
        start = src.index("EXAM_COLUMNS = ")
        block = src[start:src.index(")", start)]
        assert "assessment_period_id" in block, (
            "the report never reads the tag, so it cannot group by period")

    def test_each_report_row_carries_its_period_id(self):
        src = SCOPE.read_text(encoding="utf-8")
        assert '"period_id":' in src, "a report row has no period to be grouped by"

    def test_the_report_builds_the_period_groups(self):
        src = SCOPE.read_text(encoding="utf-8")
        assert "compare_by_period(" in src, "the report has no period grouping"
        assert '"period_groups"' in src, "the grouping never reaches the template"

    def test_the_analytics_page_shows_the_comparison(self):
        html = ANALYTICS.read_text(encoding="utf-8")
        assert "period_groups" in html, "the page shows totals but never the periods"
        assert "t('" in html


# ── both write doors tag ─────────────────────────────────────────────────────

def _call(src: str, name: str) -> str:
    return src.split(f"def {name}(")[1].split("\ndef ")[0]


class TestBothWriteDoorsTagThePaper:
    def test_the_new_exam_write_tags_the_paper(self):
        call = _call(TEACHER.read_text(encoding="utf-8"), "exam_form")
        assert "assessment_period_id" in call, (
            "a new paper is written without a period tag")

    def test_the_edit_write_tags_the_paper(self):
        call = _call(TEACHER.read_text(encoding="utf-8"), "exam_detail")
        assert "assessment_period_id" in call, (
            "an edited paper keeps a stale period tag")


# ── the schema ───────────────────────────────────────────────────────────────

class TestTheMigrationJoinsThem:
    def test_the_migration_exists(self):
        assert MIGRATION.exists(), "no migration adds exams.assessment_period_id"

    def test_it_adds_the_column_as_an_fk(self):
        import re

        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert re.search(r"ALTER TABLE exams\s+ADD COLUMN IF NOT EXISTS "
                         r"assessment_period_id UUID", sql), (
            "the column is not added idempotently")
        assert "REFERENCES public.assessment_periods(id)" in sql, (
            "the tag is not an FK, so it can name a period that does not exist")
        assert "ON DELETE SET NULL" in sql, (
            "deleting a period would delete the papers filed under it")

    def test_it_indexes_the_column(self):
        import re

        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert re.search(r"CREATE INDEX IF NOT EXISTS \w+\s+ON exams\s*"
                         r"\(assessment_period_id\)", sql)

    def test_it_backfills_idempotently_and_only_nulls(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert "assessment_period_id IS NULL" in sql, (
            "the backfill would overwrite a tag a person or a rule set")

    def test_it_is_additive_and_idempotent(self):
        import re

        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql)
        assert not re.search(r"^\s*BEGIN\s*;", sql, re.M)
        assert not re.search(r"^\s*COMMIT\s*;", sql, re.M)
