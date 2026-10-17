"""A default belongs to a year, so a new year cannot move an old mark.

Migration 058 gave a school **one** default, stored on the component itself. That is
enough while the policy never changes, and wrong the moment it does: a final mark is
recomputed from the weight map every time it is read (`grade_weighting.compute` is
pure over the weights), so changing the default for the new academic year silently
moved the subjects that *follow* the default in the years before it — marks already
handed to pupils and reported to parents.

Migration 067 makes the default per year. This file pins the resolution order, which
is the whole contract:

1. the year's own default;
2. else the nearest **earlier** year's default — the policy in force while that year
   was being graded;
3. else `grade_component_type.default_weight`, the school-wide value from before
   defaults had a year, which is what older years were graded under;
4. else nothing, and the simple mean.

A year whose date is unknown is not placed in time by guesswork: it reads its own
rows or the floor, never a neighbour's policy.
"""
from __future__ import annotations

from pathlib import Path

from app.services import grade_weighting as gw

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "app" / "routes" / "admin_sekolah.py"
TEACHER = ROOT / "app" / "routes" / "teacher.py"
SERVICE = ROOT / "app" / "services" / "grade_weighting.py"
WEIGHTS_HTML = ROOT / "app" / "templates" / "admin_sekolah" / "grade_weights.html"
MIGRATION = ROOT / "supabase" / "migrations" / "067_grade_default_by_year.sql"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    """A tiny PostgREST stand-in: filters, insert, update, and a write log."""

    def __init__(self, store, name):
        self.store, self.name = store, name
        self.filters, self.op, self.payload = [], "select", None

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
        self.filters.append((col, str(val)))
        return self

    def in_(self, col, values):
        self.filters.append((col, {str(v) for v in values}))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def _matches(self, row):
        for col, want in self.filters:
            have = row.get(col)
            if isinstance(want, set):
                if str(have) not in want:
                    return False
            elif str(have) != want:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self.op == "insert":
            batch = self.payload if isinstance(self.payload, list) else [self.payload]
            rows.extend(batch)
            self.store.setdefault("_writes", []).append((self.name, "insert", batch))
            return _Resp(batch)
        if self.op == "update":
            hit = [r for r in rows if self._matches(r)]
            for row in hit:
                row.update(self.payload)
            self.store.setdefault("_writes", []).append((self.name, "update", hit))
            return _Resp(hit)
        return _Resp([r for r in rows if self._matches(r)])


class _Fake:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables, name)

    def writes(self, name):
        return [w for w in self.tables.get("_writes", []) if w[0] == name]


def _components(**defaults):
    return [{"id": cid, "name": cid, "school_id": "s1", "is_active": True,
             "default_weight": weight} for cid, weight in defaults.items()]


def _years(*rows):
    return [{"id": yid, "name": name, "start_date": start, "school_id": "s1"}
            for yid, name, start in rows]


Y2025 = _years(("y2025", "2025/2026", "2025-07-01"),
               ("y2026", "2026/2027", "2026-07-01"))
Y2027 = _years(("y2025", "2025/2026", "2025-07-01"),
               ("y2026", "2026/2027", "2026-07-01"),
               ("y2027", "2027/2028", "2027-07-01"))


def _default_rows(year_id, **weights):
    return [{"id": f"{year_id}-{cid}", "school_id": "s1", "school_year_id": year_id,
             "component_id": cid, "weight_percent": weight, "is_active": True}
            for cid, weight in weights.items()]


def _school(**kw):
    tables = {
        "grade_component_type": _components(**kw.get("floor", {})),
        "school_years": kw.get("years", Y2025),
        "grade_default_weight": kw.get("defaults", []),
        "grade_weight_config": [],
    }
    return _Fake(tables)


class TestWhichPolicyAYearIsUnder:
    def test_a_year_with_its_own_default_uses_it(self):
        fake = _school(floor={"c1": 30, "c2": 70},
                       defaults=_default_rows("y2026", c1=100))
        assert gw.default_config(fake, "s1", "y2026") == {"c1": 100}
        assert gw.default_config(fake, "s1", "y2025") == {"c1": 30, "c2": 70}

    def test_a_new_year_never_moves_the_one_before_it(self):
        fake = _school(floor={"c1": 30, "c2": 70})
        before = gw.default_config(fake, "s1", "y2025")
        gw.table = None  # readability: the write below is the only change
        ok, _out = gw.save_defaults(fake, "s1", {"c1": 25, "c2": 75}, year_id="y2026")
        assert ok
        assert gw.default_config(fake, "s1", "y2025") == before, (
            "the year already graded keeps the weights it was graded under")
        assert gw.default_config(fake, "s1", "y2026") == {"c1": 25, "c2": 75}

    def test_a_year_with_no_default_follows_the_one_before_it(self):
        fake = _school(floor={"c1": 30, "c2": 70},
                       defaults=_default_rows("y2025", c1=40, c2=60))
        snap = gw.default_snapshot(fake, "s1", "y2026")
        assert snap["weights"] == {"c1": 40, "c2": 60}
        assert snap["source"] == "inherited"
        assert snap["from_year"]["name"] == "2025/2026"

    def test_a_year_older_than_every_versioned_default_keeps_the_school_floor(self):
        fake = _school(floor={"c1": 30, "c2": 70},
                       defaults=_default_rows("y2026", c1=100))
        snap = gw.default_snapshot(fake, "s1", "y2025")
        assert snap["weights"] == {"c1": 30, "c2": 70}, (
            "a default set for a later year must not reach back into an older one")
        assert snap["source"] == "school"

    def test_the_nearest_earlier_year_wins_not_the_latest_overall(self):
        fake = _school(floor={"c1": 100}, years=Y2027,
                       defaults=_default_rows("y2025", c1=100)
                       + _default_rows("y2026", c1=50, c2=50)
                       + _default_rows("y2027", c1=10, c2=90))
        assert gw.default_config(fake, "s1", "y2026") == {"c1": 50, "c2": 50}
        assert gw.default_config(fake, "s1", "y2027") == {"c1": 10, "c2": 90}

    def test_a_half_filled_year_is_not_a_policy_and_falls_through(self):
        fake = _school(floor={"c1": 30, "c2": 70},
                       defaults=_default_rows("y2026", c1=40))
        assert gw.default_config(fake, "s1", "y2026") == {"c1": 30, "c2": 70}, (
            "60% is not a distribution, so the previous policy answers")
        assert gw.default_snapshot(fake, "s1", "y2026")["source"] == "school"

    def test_a_year_with_no_date_does_not_borrow_a_neighbours_policy(self):
        fake = _school(floor={"c1": 30, "c2": 70},
                       years=[{"id": "y-unknown", "name": "?", "start_date": None,
                               "school_id": "s1"}] + Y2025,
                       defaults=_default_rows("y2025", c1=100))
        assert gw.default_config(fake, "s1", "y-unknown") == {"c1": 30, "c2": 70}

    def test_no_year_answers_the_school_floor(self):
        fake = _school(floor={"c1": 30, "c2": 70},
                       defaults=_default_rows("y2026", c1=100))
        assert gw.default_config(fake, "s1") == {"c1": 30, "c2": 70}, (
            "a reader with no year to name gets what it always got")

    def test_nothing_configured_is_the_simple_mean(self):
        fake = _school()
        assert gw.default_config(fake, "s1", "y2026") == {}
        assert gw.default_snapshot(fake, "s1", "y2026")["source"] == "none"

    def test_the_source_names_are_the_module_constants(self):
        fake = _school(floor={"c1": 100}, defaults=_default_rows("y2026", c1=100))
        seen = {gw.default_snapshot(fake, "s1", y)["source"] for y in ("y2026", "y2025")}
        assert seen <= set(gw.DEFAULT_SOURCES)


class TestTheYearTheMarkBelongsTo:
    def test_the_effective_config_reads_the_default_of_the_papers_year(self):
        fake = _school(floor={"c1": 100},
                       defaults=_default_rows("y2025", c1=20, c2=80))
        assert gw.effective_config(fake, "s1", "sub1", "y2025") == {"c1": 20, "c2": 80}
        assert gw.effective_config(fake, "s1", "sub1", "y2026") == {"c1": 20, "c2": 80}, (
            "a year with no default of its own inherits the one before it")
        assert gw.effective_config(fake, "s1", "sub1", None) == {"c1": 100}

    def test_a_custom_subject_config_still_beats_any_default(self):
        fake = _school(floor={"c1": 100},
                       defaults=_default_rows("y2026", c1=100))
        fake.tables["grade_weight_config"] = [
            {"id": "w1", "school_id": "s1", "subject_id": "sub1",
             "school_year_id": "y2026", "component_id": "c1",
             "weight_percent": 0, "is_active": False},
            {"id": "w2", "school_id": "s1", "subject_id": "sub1",
             "school_year_id": "y2026", "component_id": "c2",
             "weight_percent": 100, "is_active": True},
        ]
        assert gw.effective_config(fake, "s1", "sub1", "y2026") == {"c2": 100}

    def test_inherited_config_ignores_this_years_own_rows(self):
        fake = _school(floor={"c1": 100},
                       defaults=_default_rows("y2025", c1=40, c2=60)
                       + _default_rows("y2026", c1=100))
        assert gw.default_config(fake, "s1", "y2026") == {"c1": 100}
        assert gw.inherited_config(fake, "s1", "y2026") == {"c1": 40, "c2": 60}, (
            "the clear action runs its impact check against the map the year "
            "returns to, not an empty one")


class TestSavingAYearsDefault:
    def test_the_write_lands_on_the_year_and_not_on_the_component(self):
        fake = _school(floor={"c1": 30, "c2": 70})
        ok, out = gw.save_defaults(fake, "s1", {"c1": 25, "c2": 75}, year_id="y2026")
        assert ok and out["year_id"] == "y2026"
        rows = fake.tables["grade_default_weight"]
        assert {r["school_year_id"] for r in rows} == {"y2026"}
        assert {r["component_id"]: r["weight_percent"] for r in rows} == {"c1": 25, "c2": 75}
        assert not fake.writes("grade_component_type"), (
            "writing the component column would move every older year at once")

    def test_a_year_of_another_school_is_refused(self):
        fake = _school(floor={"c1": 0})
        fake.tables["school_years"] = [{**y, "school_id": "other"} for y in Y2025]
        ok, out = gw.save_defaults(fake, "s1", {"c1": 100}, year_id="y2025")
        assert not ok and out["status"] == 404
        assert not fake.tables.get("grade_default_weight")

    def test_the_total_rule_still_holds_per_year(self):
        fake = _school(floor={"c1": 0, "c2": 0})
        ok, out = gw.save_defaults(fake, "s1", {"c1": 40, "c2": 40}, year_id="y2026")
        assert not ok and out["status"] == 400 and "100" in out["error"]

    def test_an_empty_set_removes_only_this_years_policy(self):
        fake = _school(defaults=_default_rows("y2025", c1=30, c2=70)
                       + _default_rows("y2026", c1=100))
        ok, out = gw.save_defaults(fake, "s1", {}, year_id="y2026")
        assert ok and out["cleared"] is True
        kept = [r for r in fake.tables["grade_default_weight"]
                if r["school_year_id"] == "y2026"]
        assert kept and all(r["is_active"] is False for r in kept), (
            "the row stays as the record of what the year was graded under")
        assert gw.default_config(fake, "s1", "y2026") == {"c1": 30, "c2": 70}, (
            "the year goes back to inheriting, not to the simple mean")

    def test_without_a_year_the_school_floor_is_written(self):
        fake = _school(floor={"c1": 0, "c2": 0})
        ok, _out = gw.save_defaults(fake, "s1", {"c1": 100})
        assert ok
        assert not fake.tables.get("grade_default_weight")
        assert fake.writes("grade_component_type")


class TestTheWiring:
    def _defaults_route(self):
        src = ADMIN.read_text(encoding="utf-8")
        return src.split('route("/grade-weights/defaults", methods=["POST"])')[1] \
                  .split("\n@admin_sekolah_bp.route")[0]

    def test_the_route_writes_the_year_it_was_given(self):
        body = self._defaults_route()
        assert 'payload.get("school_year_id") or year.get("id")' in body, (
            "the default is written for the year the page is showing")
        assert "year_id=target_year_id" in body

    def test_the_closed_refusal_is_asked_of_the_target_year(self):
        body = self._defaults_route()
        assert "write_refusal(supabase, target_year_id)" in body, (
            "a closed year must be refused because it is the year being written, "
            "not because it happens to be the active one")

    def test_the_route_answers_with_the_policy_now_in_force(self):
        body = self._defaults_route()
        assert "fresh=True" in body, (
            "the resolver is memoised per request, so the response must read fresh "
            "or it reports the pre-save answer")

    def test_the_component_picker_reads_the_running_years_default(self):
        src = TEACHER.read_text(encoding="utf-8")
        body = src.split("def _grade_components_by_subject(")[1].split("\ndef ")[0]
        assert 'default_config(supabase, school_id, year.get("id"))' in body

    def test_the_page_sends_the_year_and_shows_where_it_came_from(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        assert "school_year_id: this.yearId" in html
        assert "data-default-source" in html
        assert "defaultSource === 'inherited'" in html and "defaultFromYear" in html

    def test_the_clear_action_runs_the_impact_check_against_the_inherited_map(self):
        html = WEIGHTS_HTML.read_text(encoding="utf-8")
        body = html.split("async clearThisYearDefault()")[1].split("\n        async ")[0]
        assert "Object.assign({}, this.inherited)" in body, (
            "an empty map would be read as the simple mean and warn about a "
            "movement that is not going to happen")
        assert "confirmReportedMoves('default', null, back)" in body

    def test_the_service_forwards_the_year_at_every_default_read(self):
        src = SERVICE.read_text(encoding="utf-8")
        bare = [line for line in src.splitlines()
                if "default_config(supabase, school_id)" in line
                or "default_config(supabase, sid)" in line]
        assert not bare, f"a reader with a year must pass it: {bare}"


class TestTheMigration:
    def test_it_creates_one_default_row_per_year_and_component(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS public.grade_default_weight" in sql
        assert "school_year_id UUID NOT NULL REFERENCES public.school_years" in sql
        assert ("CREATE UNIQUE INDEX IF NOT EXISTS idx_grade_default_unique" in sql
                and "(school_year_id, component_id)" in sql)

    def test_it_keeps_the_058_column_as_the_floor(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "default_weight" in sql
        assert "ALTER TABLE public.grade_component_type" not in sql, (
            "the migration must not rewrite the floor; that is the read path's job")

    def test_it_has_no_transaction_of_its_own(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        code = "\n".join(line for line in sql.splitlines()
                         if not line.strip().startswith("--"))
        assert "BEGIN;" not in code and "COMMIT;" not in code
