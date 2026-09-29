"""A principal reads the school's progress — and the school is the only thing they read.

Two halves, and they fail in different ways.

The **scope** half is the permission. `principal` and `vice_principal` may see
every exam in their own school and nothing else, so the questions worth holding
are the ones a plausible mistake gets wrong: an official with no school on file
(which must see *nothing*, not everything — the same shape that once handed an
admin without a school the whole box), and an official from another school (which
must see nothing of this one). What makes these answerable without a database is
that the predicate is a pure function of a row plus the session's own ids.

The **calendar** half is arithmetic, and arithmetic done in the wrong clock is
wrong by a day and looks fine: a sitting submitted at 17:30 UTC is *the next day*
in WIB, which is the day a school's calendar prints. So every bucket is asserted
against a timestamp chosen to fall either side of midnight in the school's clock.
The bucket functions take plain rows and return plain dicts, so they are held here
without a database at all.

The last thing held is what the page *is*: a read-only oversight page. The
official blueprint has no writing route today and this suite fails the day one is
added, because "read-only" has to be a property of the file rather than a promise
in a comment — which is what it says in the module's own docstring.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import pathlib
import re

import pytest

from app.services import analysis_scope, official_insight
from app.utils.exam_access import can_read_exam

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRINCIPAL_PY = (ROOT / "app" / "routes" / "principal.py").read_text(encoding="utf-8")
ANALYTICS_HTML = (ROOT / "app" / "templates" / "teacher" / "analytics.html").read_text(encoding="utf-8")
OFFICIALS = ("principal", "vice_principal")


# ── the read predicate ───────────────────────────────────────────────────────

SCHOOL_A = "11111111-1111-1111-1111-111111111111"
SCHOOL_B = "22222222-2222-2222-2222-222222222222"
EXAM_A = {"id": "exam-a", "school_id": SCHOOL_A, "teacher_id": "teacher-1"}


class TestWhoMayReadAnExam:
    @pytest.mark.parametrize("role", OFFICIALS)
    def test_an_official_reads_their_own_school(self, role):
        assert can_read_exam("official-1", role, SCHOOL_A, EXAM_A) is True

    @pytest.mark.parametrize("role", OFFICIALS)
    def test_an_official_does_not_read_another_school(self, role):
        assert can_read_exam("official-1", role, SCHOOL_B, EXAM_A) is False

    @pytest.mark.parametrize("role", OFFICIALS)
    def test_an_official_with_no_school_reads_nothing(self, role):
        """A session with no school would otherwise match every row whose
        `school_id` is empty, and an oversight page that reads nothing is the
        only honest answer for an account that belongs to no school."""
        assert can_read_exam("official-1", role, None, EXAM_A) is False

    def test_a_teacher_still_reads_only_their_own(self):
        assert can_read_exam("teacher-1", "guru", SCHOOL_A, EXAM_A) is True
        assert can_read_exam("teacher-2", "guru", SCHOOL_A, EXAM_A) is False

    def test_a_pupil_reads_nothing_through_this_door(self):
        assert can_read_exam("student-1", "murid", SCHOOL_A, EXAM_A) is False

    def test_an_empty_row_is_refused(self):
        assert can_read_exam("official-1", "principal", SCHOOL_A, None) is False


# ── the scope ────────────────────────────────────────────────────────────────

class TestTheOfficialScope:
    @pytest.mark.parametrize("role", OFFICIALS)
    def test_the_scope_knows_both_officials(self, role):
        assert role in analysis_scope.SCOPE_LABELS
        pair = analysis_scope.scope_pair(role)
        assert pair["id"] and pair["en"]
        assert pair["en"] != pair["id"], (
            "a scope that is not bilingual is the half of the page the toggle cannot reach")

    @pytest.mark.parametrize("role", OFFICIALS)
    def test_the_scope_predicate_is_the_read_one(self, role):
        """The predicate `analysis_scope` asks must be the one that admits
        officials, or the page renders an empty report with nothing wrong."""
        assert analysis_scope.can_read_exam is can_read_exam

    @pytest.mark.parametrize("role", OFFICIALS)
    def test_an_official_scope_reaches_their_own_school_and_stops(self, role):
        """The predicate alone is not enough: the row has to come *back* through
        the scope. Asking the acting predicate (`can_manage_exam`) returns nothing
        for an official — the page then renders an empty report and nothing
        anywhere goes red, which is the failure this suite exists for."""
        log: list = []
        mine = analysis_scope.exams_in_scope(_ScopeFake({"exams": [_SCOPE_EXAM]}, log),
                                             role, "official-1", SCHOOL_A)
        assert [row["id"] for row in mine] == ["exam-a"]
        theirs = analysis_scope.exams_in_scope(_ScopeFake({"exams": [_SCOPE_EXAM]}, log),
                                               role, "official-1", SCHOOL_B)
        assert theirs == [], "another school's exam was read by an official"

    @pytest.mark.parametrize("role", OFFICIALS)
    def test_the_scope_narrows_the_query_to_the_school(self, role):
        """A predicate that filters after reading the whole table is a permission,
        not a scope: on this box it is the difference between one school's rows and
        every school's."""
        log: list = []
        analysis_scope.exams_in_scope(_ScopeFake({"exams": [_SCOPE_EXAM]}, log),
                                      role, "official-1", SCHOOL_A)
        assert ("exams", "eq", "school_id", SCHOOL_A) in log

    def test_the_analytics_template_takes_its_own_base_path(self):
        """The teacher page hardcoded `/teacher/analytics` in its form action and
        its three export links, so an official reading the same template would
        have every button send them back to a page that refuses. The base path is
        a variable now, and the default keeps the teacher page unchanged."""
        assert 'action="/teacher/analytics"' not in ANALYTICS_HTML
        assert ANALYTICS_HTML.count("exportUrl('{{ analysis_base }}") == 3, (
            "the CSV, PDF and print links must all be built from the reader's own base path")
        assert "analysis_base|default('/teacher/analytics'" in ANALYTICS_HTML, (
            "the default is what keeps the teacher's own page pointing at itself")


# ── the school's own clock ───────────────────────────────────────────────────

class TestTheLocalDay:
    def test_utc_evening_is_the_next_day_in_wib(self):
        """17:30 UTC is 00:30 the next morning in WIB — the sitting belongs to
        the next day on the school's calendar, which is the day this page prints."""
        assert official_insight.local_date("2026-02-03T17:30:00+00:00", 7) == dt.date(2026, 2, 4)

    def test_a_utc_offset_of_zero_is_not_silently_applied(self):
        assert official_insight.local_date("2026-02-03T17:30:00+00:00", 0) == dt.date(2026, 2, 3)

    def test_an_unreadable_stamp_is_no_day_rather_than_today(self):
        assert official_insight.local_date("not a date", 7) is None
        assert official_insight.local_date(None, 7) is None


class TestTheIntensityLevels:
    def test_zero_stays_zero(self):
        assert official_insight.levels([0, 0, 0]) == [0, 0, 0]

    def test_the_busiest_day_reaches_the_top(self):
        assert official_insight.levels([0, 1, 2, 3, 4]) == [0, 1, 2, 3, 4]

    def test_a_flat_set_does_not_all_paint_darkest(self):
        """Every day the same is every day mid-scale, not every day at the top:
        a scale that saturates on a quiet month says nothing about a busy one."""
        assert set(official_insight.levels([2, 2, 2])) == {2}

    def test_a_lone_sitting_is_visible(self):
        assert official_insight.levels([0, 1]) == [0, 1]


class TestTheCalendarMonth:
    def test_the_grid_is_monday_first_and_seven_wide(self):
        weeks = official_insight.calendar_month({}, 2026, 2)
        assert weeks, "February 2026 has days"
        assert all(len(week) == 7 for week in weeks)

    def test_cells_outside_the_month_are_none_not_zero(self):
        """A `None` is the margin of the month; a zero is a day the school did
        nothing, and the two must not paint the same."""
        weeks = official_insight.calendar_month({}, 2026, 2)
        first = weeks[0]
        assert first[0] is None, "February 2026 starts on a Sunday"
        assert any(cell is not None for cell in first)
        assert [cell["day"] for cell in weeks[1]] == [2, 3, 4, 5, 6, 7, 8]

    def test_every_day_of_the_month_appears_exactly_once(self):
        weeks = official_insight.calendar_month({}, 2026, 2)
        days = [cell["day"] for week in weeks for cell in week if cell]
        assert sorted(days) == list(range(1, 29))

    def test_a_day_with_activity_carries_its_numbers(self):
        weeks = official_insight.calendar_month({"2026-02-04": {"sittings": 3, "submitted": 2}},
                                                2026, 2)
        cell = next(cell for week in weeks for cell in week if cell and cell["day"] == 4)
        assert cell["sittings"] == 3 and cell["submitted"] == 2
        assert cell["level"] > 0
        quiet = next(cell for week in weeks for cell in week if cell and cell["day"] == 5)
        assert quiet["level"] == 0 and quiet["sittings"] == 0


# ── the buckets ──────────────────────────────────────────────────────────────

#: A minimal exams-only database, for the two scope tests: enough chain to answer
#: `exams_in_scope` and a log, because one of those tests is about the *query*.
_SCOPE_EXAM = {"id": "exam-a", "school_id": SCHOOL_A, "teacher_id": "teacher-1",
               "created_at": "2026-02-02T01:00:00+00:00"}


class _ScopeQuery:
    def __init__(self, table, rows, log):
        self._table, self._rows, self._log = table, list(rows), log
        self._eq, self._in, self._gte = [], [], []
        self._order = None
        self._limit = None

    def select(self, columns):
        return self

    def eq(self, column, value):
        self._eq.append((column, value))
        self._log.append((self._table, "eq", column, value))
        return self

    def in_(self, column, values):
        self._in.append((column, list(values)))
        return self

    def gte(self, column, value):
        self._gte.append((column, value))
        return self

    def order(self, column, desc=False):
        return self

    def limit(self, count):
        return self

    def execute(self):
        rows = list(self._rows)
        for column, value in self._eq:
            rows = [row for row in rows if str(row.get(column)) == str(value)]
        return _Result(rows)


class _ScopeFake:
    def __init__(self, tables, log):
        self._tables, self._log = tables, log

    def table(self, name):
        return _ScopeQuery(name, self._tables.get(name, []), self._log)


def _sitting(exam_id="exam-a", teacher_id="teacher-1", *, started=None, submitted=None,
             status="submitted", mark=None, late=False):
    return {"exam_id": exam_id, "teacher_id": teacher_id, "started_at": started,
            "submitted_at": submitted, "status": status, "final_score": mark,
            "submitted_late": late}


class TestTheWeeklyBuckets:
    def test_a_sitting_lands_in_its_own_school_clock_week(self):
        """Sunday 17:30 UTC is Monday morning in WIB — the week it belongs to is
        the *next* one, and a report that used the UTC week would place it wrong
        for exactly the schools this page is for."""
        rows = [_sitting(submitted="2026-02-01T17:30:00+00:00", mark=80)]
        weeks = official_insight.weekly(rows, [], 7, today=dt.date(2026, 2, 5), weeks=2)
        assert weeks[-1]["submitted"] == 1
        assert weeks[-1]["start"] == "2026-02-02", "the sitting moved to the next ISO week"
        assert weeks[0]["submitted"] == 0

    def test_the_window_is_oldest_first_and_the_asked_length(self):
        weeks = official_insight.weekly([], [], 7, today=dt.date(2026, 2, 10), weeks=4)
        assert len(weeks) == 4
        assert [week["start"] for week in weeks] == sorted(week["start"] for week in weeks)

    def test_an_empty_week_is_a_zero_row_not_a_missing_one(self):
        weeks = official_insight.weekly([], [], 7, today=dt.date(2026, 2, 10), weeks=2)
        assert all(week["submitted"] == 0 and week["mean"] is None for week in weeks)

    def test_the_mean_is_over_the_marks_that_exist(self):
        """Three sittings are counted as submitted — two marked and one not — and
        the mean is of the two marks. A paper nobody has marked is *waiting*, not a
        zero: dividing by it would make a school look like it failed a paper it has
        simply not marked yet."""
        rows = [_sitting(submitted="2026-02-03T02:00:00+00:00", mark=70),
                _sitting(submitted="2026-02-04T02:00:00+00:00", mark=90),
                _sitting(submitted="2026-02-04T03:00:00+00:00", mark=None),
                _sitting(submitted="2026-02-04T04:00:00+00:00", mark=None, status="draft")]
        weeks = official_insight.weekly(rows, [], 7, today=dt.date(2026, 2, 5), weeks=2)
        assert weeks[-1]["submitted"] == 3
        assert weeks[-1]["mean"] == 80.0

    def test_papers_created_are_counted_beside_sittings(self):
        exams = [{"id": "exam-a", "teacher_id": "teacher-1", "created_at": "2026-02-04T01:00:00+00:00"}]
        weeks = official_insight.weekly([], exams, 7, today=dt.date(2026, 2, 5), weeks=2)
        assert weeks[-1]["exams"] == 1

    def test_a_late_paper_is_counted_as_late_in_its_bucket(self):
        rows = [_sitting(submitted="2026-02-04T02:00:00+00:00", mark=50, late=True)]
        weeks = official_insight.weekly(rows, [], 7, today=dt.date(2026, 2, 5), weeks=2)
        assert weeks[-1]["late"] == 1


class TestTheMonthlyBuckets:
    def test_months_are_named_and_ordered_oldest_first(self):
        months = official_insight.monthly([], [], 7, today=dt.date(2026, 2, 10), months=3)
        assert [month["key"] for month in months] == ["2025-12", "2026-01", "2026-02"]

    def test_a_month_with_no_sitting_is_a_zero_row_not_a_missing_one(self):
        months = official_insight.monthly([], [], 7, today=dt.date(2026, 2, 10), months=3)
        assert all(month["submitted"] == 0 for month in months)

    def test_a_sitting_is_counted_in_its_local_month(self):
        """31 January 20:00 UTC is 1 February in WIB. Counting it in January is
        the mistake this test exists to make impossible."""
        rows = [_sitting(submitted="2026-01-31T20:00:00+00:00", mark=70)]
        months = official_insight.monthly(rows, [], 7, today=dt.date(2026, 2, 10), months=2)
        by_key = {month["key"]: month for month in months}
        assert by_key["2026-02"]["submitted"] == 1
        assert by_key["2026-01"]["submitted"] == 0


# ── the page's own reader ────────────────────────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data
        self.count = len(data)


class _Query:
    """Only the chains `official_insight.progress` actually uses."""

    def __init__(self, table, rows, log, *, honours_eq=True):
        self._table, self._rows, self._log = table, list(rows), log
        self._eq, self._in, self._gte = [], [], []
        self._order = None
        self._limit = None
        self._honours_eq = honours_eq

    def select(self, columns):
        self._log.append((self._table, "select", columns))
        return self

    def eq(self, column, value):
        self._eq.append((column, value))
        self._log.append((self._table, "eq", column, value))
        return self

    def in_(self, column, values):
        self._in.append((column, list(values)))
        self._log.append((self._table, "in_", column, list(values)))
        return self

    def gte(self, column, value):
        self._gte.append((column, value))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def execute(self):
        rows = list(self._rows)
        for column, value in self._eq:
            if self._honours_eq:
                rows = [row for row in rows if str(row.get(column)) == str(value)]
        for column, values in self._in:
            wanted = {str(value) for value in values}
            rows = [row for row in rows if str(row.get(column)) in wanted]
        for column, value in self._gte:
            rows = [row for row in rows if str(row.get(column) or "") >= str(value)]
        if self._order:
            column, desc = self._order
            rows.sort(key=lambda row: str(row.get(column) or ""), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class _FakeSupabase:
    """`honours_eq=False` hands the reader rows a real database *should* have
    filtered — which is the only way to tell a second line of defence from a
    comment. The page's own filter is unreachable while the fake filters first,
    and that is how this suite passed while the live page counted nothing."""

    def __init__(self, tables, log, *, honours_eq=True):
        self._tables, self._log = tables, log
        self._honours_eq = honours_eq

    def table(self, name):
        return _Query(name, self._tables.get(name, []), self._log,
                      honours_eq=self._honours_eq)


SCHOOL_EXAM = {"id": "exam-a", "school_id": SCHOOL_A, "teacher_id": "teacher-1",
               "title": "Matematika", "subject": "MTK", "status": "active",
               "created_at": "2026-02-02T01:00:00+00:00"}


def _tables():
    return {
        "exams": [SCHOOL_EXAM],
        "submissions": [
            {"exam_id": "exam-a", "student_id": "s1", "status": "submitted",
             "final_score": 80, "score": 80, "started_at": "2026-02-03T02:00:00+00:00",
             "submitted_at": "2026-02-03T03:00:00+00:00", "submitted_late": False},
        ],
        "profiles": [{"id": "teacher-1", "full_name": "Bu Ani", "school_id": SCHOOL_A}],
    }


class TestWhatThePageReads:
    def test_the_reader_only_asks_for_its_own_school(self):
        log = []
        official_insight.progress(_FakeSupabase(_tables(), log), SCHOOL_A,
                                  today=dt.date(2026, 2, 10))
        assert ("exams", "eq", "school_id", SCHOOL_A) in log, (
            "the page must narrow the exams by the reader's own school, never by a value a client sent")

    def test_a_row_from_another_school_is_dropped_even_if_it_arrives(self):
        """The second line of defence, and the one that was broken on the live box.

        `progress` re-checks each row's school after the query. That check reads
        `exam.get("school_id")`, and the select list did not name the column — so
        PostgREST left it out, every row's value was `None`, every row was
        dropped, and a school with four papers in it was shown an empty month with
        no error anywhere. The fake here therefore *does not* filter the way a
        database would: the point is that a row the query should never have
        returned still cannot be counted.
        """
        tables = _tables()
        tables["exams"] = [SCHOOL_EXAM, {**SCHOOL_EXAM, "id": "exam-b",
                                         "school_id": SCHOOL_B,
                                         "title": "Not our school"}]
        data = official_insight.progress(_FakeSupabase(tables, [], honours_eq=False),
                                         SCHOOL_A, today=dt.date(2026, 2, 10),
                                         month="2026-02")
        assert data["totals"]["exams"] == 1
        assert "school_id" in official_insight.EXAM_COLUMNS, (
            "the filter reads a column the select does not name, so it reads None")

    def test_a_school_with_nothing_on_file_is_an_empty_page_not_an_error(self):
        data = official_insight.progress(_FakeSupabase({}, []), SCHOOL_A,
                                         today=dt.date(2026, 2, 10))
        assert data["totals"]["submitted"] == 0
        assert data["no_data"] is True
        assert data["calendar"]["weeks"], "an empty month is still a grid"


    def test_a_month_with_no_school_is_refused_rather_than_answered(self):
        """Asserted on the *log*, not on the totals: a reader with no school whose
        query quietly runs against the empty string still answers zeroes, and zeroes
        are what a real answer looks like — so the check is that nothing was asked."""
        log: list = []
        data = official_insight.progress(_FakeSupabase(_tables(), log), None,
                                         today=dt.date(2026, 2, 10))
        assert data["totals"]["submitted"] == 0 and data["no_data"] is True
        assert log == [], "a reader with no school asked the database anyway"


# ── the page ─────────────────────────────────────────────────────────────────

class TestTheOfficialBlueprint:
    ROUTES = re.findall(r"@principal_bp\.route\(([^)]*)\)", PRINCIPAL_PY)

    def test_the_blueprint_has_routes_to_judge(self):
        assert self.ROUTES, "the official blueprint lost every route"

    def test_the_head_of_school_prefix_has_no_writing_route(self):
        """`/principal/*` is read-only by construction — the head of school oversees.

        The one delegated write authority this blueprint carries — the invigilation
        schedule — lives entirely under `/vice-principal/*`, so the separation is a
        property of the *addresses* rather than a flag inside a template. That is why
        this assertion is about the prefix and not about the blueprint: `for spec in
        self.ROUTES` used to forbid every write here, which made a delegated authority
        impossible to express anywhere except by breaking this guard.
        """
        writing = [spec for spec in self.ROUTES
                   if re.search(r"(POST|PUT|PATCH|DELETE)", spec)]
        offenders = [spec for spec in writing if spec.lstrip("(").lstrip("'").startswith("/principal/")]
        assert not offenders, (
            f"the head of school's own prefix writes: {offenders}")
        for spec in writing:
            assert "invigilation" in spec or "retake" in spec, (
                f"a writing route outside the one delegated authority: {spec}")

    @pytest.mark.parametrize("path", ["/principal/analytics", "/vice-principal/analytics",
                                      "/principal/progress", "/vice-principal/progress"])
    def test_the_page_exists(self, app, path):
        assert path in {rule.rule for rule in app.url_map.iter_rules()}

    def test_both_halves_of_the_pair_are_served_by_the_same_view(self):
        """Two readers, two addresses, one page: a second implementation is how
        the two officials' numbers start to differ."""
        assert PRINCIPAL_PY.count("def _analytics_html(") == 1
        assert PRINCIPAL_PY.count("def _progress(") == 1

    def test_the_official_routes_are_guarded(self):
        """No official page is open to any signed-in reader.

        Three guards, and each is narrowing rather than weaker:
        `school_official_required` admits both oversight roles, while
        `principal_required` and `vice_principal_required` admit only one — which is
        what makes "the head of school reads, the deputy writes" expressible.
        """
        allowed = ("school_official_required", "principal_required",
                   "vice_principal_required")
        for block in PRINCIPAL_PY.split("@principal_bp.route")[1:]:
            guard = block.split("\ndef ")[0]
            assert any(name in guard for name in allowed), (
                "an official route without its guard is a page any signed-in reader "
                f"can open: {guard.strip()[:80]}")


@contextlib.contextmanager
def _signed_in(app, role="principal", path="/principal/progress", school=SCHOOL_A):
    """The page is inside the app shell, and the shell only exists for a signed-in
    reader — so a render without a session would measure the login page instead."""
    from flask import g
    with app.test_request_context(path):
        g.user_id = "official-1"
        g.user_name = "Kepala Sekolah"
        g.user_email = "kepsek@example.test"
        g.user_role = role
        g.user_school_id = school
        g.tz_offset = 7
        g.show = {}
        yield


class TestTheAnalyticsDoor:
    """Each official's buttons must point at their own address.

    The page is shared, so the base path is the one thing that decides whether a
    deputy who presses *Export CSV* gets their own file or a 403 from the head's
    route. It is asserted by calling the view and reading what it handed Jinja —
    a source grep would pass a route that computed the right path and passed the
    wrong variable.
    """

    @pytest.mark.parametrize("role,expected", [
        ("principal", "/principal/analytics"),
        ("vice_principal", "/vice-principal/analytics")])
    def test_each_official_exports_through_their_own_door(self, monkeypatch, app,
                                                          role, expected):
        from app.routes import principal as mod

        handed: dict = {}
        report = {"rows": [], "bins": [], "totals": {"exams": 0, "participants": 0,
                                                       "mean": None, "sd": None,
                                                       "pass_rate": None}}
        monkeypatch.setattr(mod, "get_supabase", lambda: object())
        monkeypatch.setattr(mod, "_scope_report", lambda *a, **k: report)
        monkeypatch.setattr(mod, "render_template",
                            lambda name, **kwargs: handed.update(kwargs) or "")

        path = "/principal/analytics" if role == "principal" else "/vice-principal/analytics"
        view = getattr(mod, "vice_principal_analytics" if role == "vice_principal"
                       else "principal_analytics")
        with _signed_in(app, role=role, path=path):
            # The guard is what a browser meets first, and it needs a session
            # cookie this test does not have — so the view behind it is what is
            # being measured here, which is the half that builds the links.
            view.__wrapped__()

        assert handed.get("analysis_base") == expected

    @pytest.mark.parametrize("role,path", [("principal", "/principal/analytics"),
                                           ("vice_principal", "/vice-principal/analytics")])
    def test_a_reader_with_no_school_is_sent_away_instead_of_every_school(
            self, monkeypatch, app, role, path):
        from app.routes import principal as mod

        monkeypatch.setattr(mod, "get_supabase", lambda: object())
        render = []
        monkeypatch.setattr(mod, "render_template",
                            lambda *a, **k: render.append(k) or "")
        view = getattr(mod, "vice_principal_analytics" if role == "vice_principal"
                       else "principal_analytics")

        with _signed_in(app, role=role, path=path, school=None):
            response = view.__wrapped__()

        assert response.status_code == 302 and response.headers["Location"] == "/auth/login"
        assert not render, "an official with no school must not reach the database at all"


class TestTheProgressPage:
    PATH = ROOT / "app" / "templates" / "principal" / "progress.html"

    @pytest.fixture(scope="class")
    def data(self):
        return official_insight.progress(_FakeSupabase(_tables(), []), SCHOOL_A,
                                         today=dt.date(2026, 2, 10), month="2026-02")

    def test_the_file_exists(self):
        assert self.PATH.exists(), "the officials' progress page is missing"

    def test_it_extends_the_shared_chrome(self):
        assert self.PATH.read_text(encoding="utf-8").startswith("{% extends \"base.html\" %}")

    @pytest.mark.parametrize("role", OFFICIALS)
    def test_it_renders_with_the_reader_s_own_report(self, app, data, role):
        with _signed_in(app, role=role):
            html = app.jinja_env.get_template("principal/progress.html").render(
                role=role, progress=data, school={"name": "SMP Negeri 1"},
                month_key=data["month"]["key"], tz=7, base="/principal")
        assert "{{" not in html and "{%" not in html
        assert "Undefined" not in html
        assert len(re.findall(r'data-calendar-day="', html)) == data["calendar"]["days"]

    def test_the_day_cells_the_month_does_not_own_are_not_drawn(self, app, data):
        with _signed_in(app):
            html = app.jinja_env.get_template("principal/progress.html").render(
                role="principal", progress=data, school={},
                month_key=data["month"]["key"], tz=7, base="/principal")
        assert len(re.findall(r'data-calendar-day="', html)) == 28, (
            "February 2026 has 28 days on the sheet and no margin cells")
