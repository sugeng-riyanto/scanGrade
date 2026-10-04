"""KKM — the school's minimum mastery mark — belongs to a subject, not to a paper.

Fase 0 found where the number lives today: **`exams.passing_score`**, a per-paper
integer defaulting to 70, read in ~27 places to decide lulus / tidak lulus. That is
why a school cannot state the thing it actually knows — "Geography passes at 70 in
year 7 and 75 in year 9" — and why the same subject can pass at a different mark in
two rooms of one building.

So KKM becomes a row: one per (subject, school year), with an **optional** grade
level. `grade_level IS NULL` is the subject's general mark; a row *with* a level is
an override for that level only. That nullable dimension is the design decision
Fase 0 asked to be reported, and it is the granular option — a school that only
wants one mark per subject simply never writes an override, so the simpler model is
a subset rather than a second schema.

What must not change: **marks already awarded.** A result printed last year was
decided by the mark in force then, so the resolution helper takes the year as an
argument and never reaches for "the current one". A paper keeps its own
`passing_score`, snapshotted when it was created, which is why rewiring the reports
is a later step rather than a rewrite of history.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "subject_kkm.py"
MIGRATION = ROOT / "supabase" / "migrations" / "051_subject_kkm.sql"


# ── a fake that really filters, so a missing scope is a real refusal ─────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.payload = None
        self.filters = []
        self.mode = "select"

    def select(self, *a, **k):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.mode, self.payload = "update", payload
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def is_(self, column, value):
        # `is_("grade_level", "null")` is SQL's `IS NULL`, which is not equality:
        # a fake that compared it as a string would never find the general row.
        self.filters.append((column, ("is", value)))
        return self

    def in_(self, column, values):
        self.filters.append((column, list(values)))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def _matches(self, row):
        for key, value in self.filters:
            if isinstance(value, tuple) and value and value[0] == "is":
                wants_null = str(value[1]).lower() == "null"
                if (row.get(key) is None) != wants_null:
                    return False
            elif isinstance(value, list):
                if str(row.get(key)) not in [str(v) for v in value]:
                    return False
            else:
                # A `None` filter is how a caller asks for the *general* row: the
                # general row is the one whose grade_level is null.
                if value is None:
                    if row.get(key) is not None:
                        return False
                elif str(row.get(key)) != str(value):
                    return False
        return True

    def execute(self):
        self.store.calls.append((self.table, tuple(self.filters), self.payload))
        if self.table in self.store.fail:
            raise RuntimeError("boom")
        rows = self.store.rows.setdefault(self.table, [])
        if self.mode == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"r{len(rows) + 1}")
            rows.append(row)
            return _Resp([row])
        if self.mode == "update":
            hit = [r for r in rows if self._matches(r)]
            for row in hit:
                row.update(self.payload)
            return _Resp(hit)
        if self.mode == "delete":
            hit = [r for r in rows if self._matches(r)]
            self.store.rows[self.table] = [r for r in rows if r not in hit]
            return _Resp(hit)
        return _Resp([dict(r) for r in rows if self._matches(r)])


class FakeSupabase:
    def __init__(self, rows=None, fail=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.fail = set(fail)
        self.calls = []

    def table(self, name):
        return _Query(self, name)


SCHOOL = "s1"
SUBJECT = "sub-geografi"
YEAR = "y-2026"


def _years(status="active"):
    return {"school_years": [{"id": YEAR, "school_id": SCHOOL, "status": status}]}


# ── the resolution order ─────────────────────────────────────────────────────

class TestTheEffectiveMark:
    def test_a_subject_with_no_row_reads_the_default(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows=_years())
        assert kkm.effective(sb, SCHOOL, SUBJECT, year_id=YEAR) == kkm.DEFAULT_KKM == 70

    def test_the_subjects_own_mark_wins_over_the_default(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows={**_years(), "subject_kkm": [
            {"id": "k1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": None, "kkm": 75},
        ]})
        assert kkm.effective(sb, SCHOOL, SUBJECT, year_id=YEAR) == 75

    def test_a_grade_override_wins_over_the_subjects_mark(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows={**_years(), "subject_kkm": [
            {"id": "k1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": None, "kkm": 70},
            {"id": "k2", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": "9", "kkm": 80},
        ]})
        assert kkm.effective(sb, SCHOOL, SUBJECT, grade_level="9", year_id=YEAR) == 80
        assert kkm.effective(sb, SCHOOL, SUBJECT, grade_level="7", year_id=YEAR) == 70

    def test_a_grade_level_is_compared_as_text(self):
        """"7" from a form and 7 from Postgres are one level, not two."""
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows={**_years(), "subject_kkm": [
            {"id": "k2", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": "9", "kkm": 80},
        ]})
        assert kkm.effective(sb, SCHOOL, SUBJECT, grade_level=9, year_id=YEAR) == 80

    def test_another_schools_row_is_not_read(self):
        """A mark is one school's policy; reading a neighbour's is the bug this
        whole module exists to make impossible."""
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows={**_years(), "subject_kkm": [
            {"id": "k1", "school_id": "s2", "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": None, "kkm": 99},
        ]})
        assert kkm.effective(sb, SCHOOL, SUBJECT, year_id=YEAR) == 70

    def test_the_year_is_part_of_the_question(self):
        """A mark set for next year must not answer for this one."""
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows={"school_years": [
            {"id": YEAR, "school_id": SCHOOL, "status": "active"},
            {"id": "y-next", "school_id": SCHOOL, "status": "draft"},
        ], "subject_kkm": [
            {"id": "k1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": "y-next", "grade_level": None, "kkm": 88},
        ]})
        assert kkm.effective(sb, SCHOOL, SUBJECT, year_id=YEAR) == 70
        assert kkm.effective(sb, SCHOOL, SUBJECT, year_id="y-next") == 88

    def test_a_read_that_fails_still_answers_the_default(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows=_years(), fail=("subject_kkm",))
        assert kkm.effective(sb, SCHOOL, SUBJECT, year_id=YEAR) == 70


# ── writing one ──────────────────────────────────────────────────────────────

class TestWritingAMark:
    def test_the_general_mark_is_written_with_the_callers_school(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows=_years())
        out = kkm.set_kkm(sb, SCHOOL, SUBJECT, 78, year_id=YEAR)

        assert out["ok"] is True
        table, _filters, payload = sb.calls[-1]
        assert table == "subject_kkm" and payload["school_id"] == SCHOOL
        assert payload["kkm"] == 78 and payload["grade_level"] is None

    def test_a_value_outside_the_range_is_refused_before_any_write(self):
        from app.services import subject_kkm as kkm

        for bad in (-1, 101, "seratus"):
            sb = FakeSupabase(rows=_years())
            out = kkm.set_kkm(sb, SCHOOL, SUBJECT, bad, year_id=YEAR)
            assert out["ok"] is False and out["reason"] == "bad_value", bad
            assert not sb.calls, f"{bad} reached the database"

    def test_editing_a_mark_for_a_closed_year_is_refused(self):
        """A finished year's marks are history: the mark that decided them must
        not move afterwards."""
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows=_years(status="closed"))
        out = kkm.set_kkm(sb, SCHOOL, SUBJECT, 60, year_id=YEAR)
        assert out["ok"] is False and out["reason"] == "year_closed"
        assert sb.calls and all(c[0] == "school_years" for c in sb.calls), (
            "a refused write still touched subject_kkm")

    def test_setting_the_same_mark_twice_edits_the_row_rather_than_stacking_one(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows={**_years(), "subject_kkm": [
            {"id": "k1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": None, "kkm": 70},
        ]})
        kkm.set_kkm(sb, SCHOOL, SUBJECT, 75, year_id=YEAR)

        rows = sb.rows["subject_kkm"]
        assert len(rows) == 1 and rows[0]["kkm"] == 75

    def test_a_grade_override_is_a_row_of_its_own(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows=_years())
        kkm.set_kkm(sb, SCHOOL, SUBJECT, 80, grade_level="9", year_id=YEAR)

        rows = sb.rows["subject_kkm"]
        assert len(rows) == 1 and str(rows[0]["grade_level"]) == "9"

    def test_clearing_an_override_returns_the_level_to_the_subjects_mark(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows={**_years(), "subject_kkm": [
            {"id": "k1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": None, "kkm": 70},
            {"id": "k2", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": "9", "kkm": 80},
        ]})
        out = kkm.clear_override(sb, SCHOOL, SUBJECT, "9", year_id=YEAR)

        assert out["ok"] is True
        assert kkm.effective(sb, SCHOOL, SUBJECT, grade_level="9", year_id=YEAR) == 70


# ── the page's own list ──────────────────────────────────────────────────────

class TestTheListForThePage:
    def test_the_page_reads_every_mark_of_one_year_and_school(self):
        from app.services import subject_kkm as kkm

        sb = FakeSupabase(rows={**_years(), "subject_kkm": [
            {"id": "k1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": None, "kkm": 75},
            {"id": "k2", "school_id": "s2", "subject_id": SUBJECT,
             "school_year_id": YEAR, "grade_level": None, "kkm": 99},
        ]})
        rows = kkm.list_kkm(sb, SCHOOL, year_id=YEAR)

        assert [r["kkm"] for r in rows] == [75], rows
        _table, filters, _payload = sb.calls[-1]
        assert ("school_id", SCHOOL) in filters and ("school_year_id", YEAR) in filters


# ── the routes ───────────────────────────────────────────────────────────────

class TestTheRoutes:
    ROUTES = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8-sig")

    def test_the_crud_routes_are_registered(self):
        assert "/subjects/<subject_id>/kkm" in self.ROUTES
        assert "/subjects/<subject_id>/kkm/<grade_level>/clear" in self.ROUTES

    def test_every_write_declares_the_admin_door_and_the_school(self):
        starts = [m.start() for m in re.finditer(r"@admin_sekolah_bp\.route\(", self.ROUTES)]
        starts.append(len(self.ROUTES))
        seen = 0
        for start, end in zip(starts, starts[1:]):
            block = self.ROUTES[start:end]
            if "/kkm" not in block:
                continue
            seen += 1
            assert "@admin_sekolah_required" in block, block[:90]
            assert "_school_id()" in block, block[:90]
            for stolen in ('request.form.get("school_id")',
                           'request.args.get("school_id")'):
                assert stolen not in block, "the school is read from the request"
        assert seen >= 2, "expected the write routes to be found"


# ── the schema ───────────────────────────────────────────────────────────────

class TestTheSchema:
    def test_the_table_carries_its_school_and_its_year(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert "CREATE TABLE IF NOT EXISTS public.subject_kkm" in sql
        assert re.search(r"school_id UUID NOT NULL REFERENCES public\.schools\(id\)", sql)
        assert "school_year_id" in sql
        assert "ENABLE ROW LEVEL SECURITY" in sql

    def test_a_null_grade_level_is_its_own_unique_key(self):
        """Postgres treats NULLs as distinct, so the plain unique index would let a
        subject stack two *general* marks — and then 'the' general mark depends on
        row order. Two partial indexes make both keys real."""
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert re.search(r"ON public\.subject_kkm \([^)]*\)\s*WHERE grade_level IS NULL", sql), (
            "nothing stops two general marks for one subject and year")
        assert re.search(r"ON public\.subject_kkm \([^)]*\)\s*WHERE grade_level IS NOT NULL", sql), (
            "nothing stops two overrides for the same level")

    def test_the_range_is_constrained_in_the_database_too(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert re.search(r"CHECK \(kkm BETWEEN 0 AND 100\)", sql)

    def test_every_policy_names_the_caller_and_the_school(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        policies = re.findall(r'CREATE POLICY "([^"]+)" ON public\.(\w+)\s+'
                              r"FOR (SELECT|INSERT|UPDATE|DELETE|ALL)\s+TO (\w+)", sql)
        assert policies
        assert not [p for p in policies if p[3] != "authenticated"]
        for name, _table, _verb, _role in policies:
            body = sql.split(f'CREATE POLICY "{name}"')[1].split(";")[0]
            assert "public._user_school_id()" in body, name

    def test_the_migration_is_additive_and_idempotent(self):
        sql = MIGRATION.read_text(encoding="utf-8-sig")
        assert not re.search(r"DROP\s+(TABLE|COLUMN)", sql)
        assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", sql, re.M)
        assert not re.findall(r"CREATE TABLE (?!IF NOT EXISTS)", sql)
        for policy in re.findall(r'CREATE POLICY "([^"]+)"', sql):
            assert f'DROP POLICY IF EXISTS "{policy}"' in sql, policy


# ── the class-subject default, from Fase 6 ───────────────────────────────────

class TestSubjectsApplyToEveryClassUntilTurnedOff:
    """Asked for explicitly: every subject ticked for every class by default, and
    an admin un-ticks the pairs that do not apply (Geography in a language class).

    The honest shape of "default on" is **absence means yes**: a pair is offered
    unless a row says it is not. Backfilling every pair would write M×N rows for a
    school on its first day, and the next class created would be missing from it.
    """

    def test_a_pair_with_no_row_is_offered(self):
        from app.services import subject_levels as sl

        sb = FakeSupabase(rows={**_years(), "class_subjects": []})
        assert sl.mapped_class_ids(sb, SCHOOL, SUBJECT, all_class_ids=["c1", "c2"]) == {"c1", "c2"}

    def test_an_explicitly_closed_pair_is_not_offered(self):
        from app.services import subject_levels as sl

        sb = FakeSupabase(rows={**_years(), "class_subjects": [
            {"id": "m1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "class_id": "c2", "is_active": False},
        ]})
        assert sl.mapped_class_ids(sb, SCHOOL, SUBJECT, all_class_ids=["c1", "c2"]) == {"c1"}

    def test_the_caller_without_a_class_list_still_gets_the_stored_rows(self):
        """No class list, no complement to take: the caller asked the narrow
        question and gets the narrow answer."""
        from app.services import subject_levels as sl

        sb = FakeSupabase(rows={**_years(), "class_subjects": [
            {"id": "m1", "school_id": SCHOOL, "subject_id": SUBJECT,
             "class_id": "c2", "is_active": True},
        ]})
        assert sl.mapped_class_ids(sb, SCHOOL, SUBJECT) == {"c2"}


# ── the page's own wiring ────────────────────────────────────────────────────

class TestTheSubjectsPageShowsTheMark:
    """The page must carry the KKM to the card and say "X of Y classes", or the
    mark is a table nobody can read. These are source guards: the page renders
    against Supabase, which the fakes above do not stand in for."""

    PAGE = ROOT / "app" / "templates" / "admin_sekolah" / "subjects.html"
    ROUTE = ROOT / "app" / "routes" / "admin_sekolah.py"

    def test_the_card_opens_the_kkm_panel_with_its_own_mark_and_overrides(self):
        html = self.PAGE.read_text(encoding="utf-8-sig")
        assert "openKkm(" in html
        # The card reads its mark and overrides from the maps the route seeds,
        # so a save moves the badge in place instead of needing a reload. The
        # intent is unchanged: the panel opens with *this* subject's values.
        assert "kkmValue[" in html and "kkmOverrides[" in html
        src = self.ROUTE.read_text(encoding="utf-8-sig")
        assert "kkm_values=" in src and "overrides_by_subject=" in src, (
            "the page reads maps the route never renders")

    def test_the_page_offers_a_grade_override(self):
        html = self.PAGE.read_text(encoding="utf-8-sig")
        assert "saveKkm(" in html and "clearKkm(" in html
        assert "/kkm/" in html, "the clear route is never called"

    def test_the_route_passes_the_year_and_the_default_mark(self):
        src = self.ROUTE.read_text(encoding="utf-8-sig")
        assert "kkm_service.list_kkm(" in src
        assert "kkm_default=" in src and "grade_levels=" in src
        assert "total_classes=total_classes" in src

    def test_a_subject_offered_by_every_class_reads_as_all_of_them(self):
        """`class_count` is Y minus the switched-off pairs, never the row count —
        a school that never opened the panel has zero rows and must still read
        "all classes", because that is what it means."""
        src = self.ROUTE.read_text(encoding="utf-8-sig")
        assert "max(total_classes - closed, 0)" in src

    def test_the_page_marks_a_closed_year_read_only(self):
        src = self.ROUTE.read_text(encoding="utf-8-sig")
        assert "year_status=year_status" in src
        html = self.PAGE.read_text(encoding="utf-8-sig")
        assert "kkmClosed" in html


# ── Fase 7: the assignment matrix respects the offering ───────────────────────

class TestAnAssignmentRespectsWhetherTheSubjectIsOffered:
    """A subject switched off for one class cannot be assigned there. This is the
    cross-check between the two tables: the offering says *where a subject is
    taught*, the assignment says *who teaches it* — and the second must not name a
    room the first has closed."""

    def _school(self, **extra):
        rows = {
            "profiles": [{"id": "t1", "role": "guru", "school_id": SCHOOL}],
            "classes": [{"id": "c1", "school_id": SCHOOL},
                        {"id": "c2", "school_id": SCHOOL}],
            "subjects": [{"id": "s1", "school_id": SCHOOL}],
            "teacher_assignments": [],
        }
        rows.update(extra)
        return FakeSupabase(rows=rows)

    def test_a_pair_with_no_row_is_offered_and_can_be_assigned(self):
        from app.services import teacher_assignments as ta

        sb = self._school(class_subjects=[])
        assert ta.validate_targets(sb, SCHOOL, "t1", {("c1", "s1")}) is None

    def test_a_subject_switched_off_for_the_class_is_refused(self):
        from app.services import teacher_assignments as ta

        sb = self._school(class_subjects=[
            {"id": "m1", "school_id": SCHOOL, "subject_id": "s1",
             "class_id": "c1", "is_active": False},
        ])
        error = ta.validate_targets(sb, SCHOOL, "t1", {("c1", "s1")})
        assert error and "diaktifkan" in error, error

    def test_the_same_subject_in_another_class_is_still_assignable(self):
        """Switching Geography off for the language class must not close it for
        8B — the refusal is per pair, not per subject."""
        from app.services import teacher_assignments as ta

        sb = self._school(class_subjects=[
            {"id": "m1", "school_id": SCHOOL, "subject_id": "s1",
             "class_id": "c1", "is_active": False},
        ])
        assert ta.validate_targets(sb, SCHOOL, "t1", {("c2", "s1")}) is None
