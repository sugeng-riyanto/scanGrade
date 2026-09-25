"""What one sitting cost, as a measurement rather than as a count of what was seen.

The dashboard this feeds is descriptive by design, so the numbers it reads have to
be honest in a specific way: **a metric nobody measured must not be written as
zero.** A zero is a finding ("this student left the screen twice"); a missing
measurement is the absence of a finding, and a table that prints both as `0`
teaches a teacher to read the second as the first. Before this release the only
per-attempt record was `violation_logs` — a row per charged absence, with the
server's own timestamp and no client clock, no sequence, no device context and no
duration unless the second chance happened to time it. Everything else the product
wants to show (sync gaps, offline periods, answer changes, per-question dwell) had
**no source at all**, which is a different thing from being zero.

So this file pins four things:

1. **The arithmetic of a summary** — away buckets, the two clocks, drift, the
   1000-event cap, and the rule that an unmeasured metric is `None`.
2. **The baseline** — percentiles from the one method this app already owns
   (`exam_report.percentiles`), a small-class flag, and no rows for a metric that
   nobody measured.
3. **The migration** — three tables, RLS with a service-only policy on each, the
   uniqueness that makes a replay impossible, idempotent and non-destructive.
4. **The wiring** — a finished sitting records its summary, and a summary that
   cannot be written never costs a student their submission.

The percentile convention is deliberately not re-derived here: a baseline whose
p25 is not the report page's p25 is a baseline nobody can check.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services import attempt_summary as A
from app.services.exam_report import percentiles

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "035_attempt_session_events.sql"

#: The three tables the phase introduces, and the names the code must use.
EVENT_TABLE = "attempt_session_events"
SUMMARY_TABLE = "attempt_summary"
BASELINE_TABLE = "exam_class_baseline"


# ── the two row shapes the summary reads ─────────────────────────────────────

def violation(kind="tab_switch", at="2026-09-24T01:00:00+00:00", away=None, exam="e-1"):
    """A `violation_logs` row as PostgREST returns one.

    Carries `exam_id` because the reader scopes by it: a log is read for one
    student on one paper, and a summary assembled from another exam's rows would
    quietly measure the wrong sitting.
    """
    meta = {} if away is None else {"away_seconds": away}
    return {"user_id": "u-1", "exam_id": exam, "violation_type": kind,
            "created_at": at, "metadata": meta}


def session(seq, kind="tab_switch", occurred="2026-09-24T01:00:00+00:00",
            server="2026-09-24T01:00:00+00:00", duration_ms=9000,
            question_index=None, offline=False):
    """A row of the new event table."""
    return {"seq": seq, "kind": kind, "occurred_at": occurred, "server_at": server,
            "duration_ms": duration_ms, "question_index": question_index,
            "offline": offline}


# ── 1. the arithmetic ────────────────────────────────────────────────────────

class TestTheSummaryOfASitting:
    def test_nothing_recorded_is_unmeasured_rather_than_zero(self):
        """The rule the whole table rests on.

        A sitting with no events has no finding about sync, offline, answer
        changes or per-question dwell. Writing `0` there would be this app
        inventing four clean measurements out of an empty table.
        """
        payload = A.summarize([], sources=[])
        assert payload["events_seen"] == 0
        assert payload["away_count"] == 0
        assert payload["sync_gap_count"] is None
        assert payload["offline_ms"] is None
        assert payload["answer_change_count"] is None
        assert payload["per_question"] is None
        assert payload["effective_active_ms"] is None

    def test_a_short_absence_and_a_long_one_are_counted_apart(self):
        """`< 10 s` is the boundary the exam page's own countdown uses."""
        away = A.summarize([violation(away=4), violation(away=30)], sources=["violation_logs"])
        assert away["away_count"] == 2
        assert away["away_short_count"] == 1
        assert away["away_long_count"] == 1
        assert away["away_total_ms"] == 34_000
        assert away["away_max_ms"] == 30_000
        assert away["away_unknown_count"] == 0

    def test_an_absence_recorded_on_sight_has_no_duration_and_is_not_a_zero(self):
        """The fullscreen panel and the third absence are charged without a length.

        `metadata.away_seconds` absent means "not measured", which is why the row
        is counted as an absence **and** as an unknown duration rather than as a
        zero-second one.
        """
        away = A.summarize([violation(away=None), violation(away=10)], sources=["violation_logs"])
        assert away["away_count"] == 2
        assert away["away_unknown_count"] == 1
        assert away["away_total_ms"] == 10_000

    def test_the_event_count_is_bounded_and_a_bounded_summary_says_so(self):
        """1000 events per attempt, and the summary admits when it hit the wall.

        A silently truncated summary reads exactly like a complete one, which is
        how a teacher loses the last stretch of a sitting without knowing.
        """
        events = [session(i) for i in range(1, A.MAX_EVENTS + 2)]
        payload = A.summarize(events, sources=[EVENT_TABLE])
        assert payload["events_seen"] == A.MAX_EVENTS + 1
        assert payload["truncated"] is True
        assert payload["away_count"] == A.MAX_EVENTS

    def test_a_summary_under_the_cap_is_not_flagged_as_truncated(self):
        payload = A.summarize([session(1)], sources=[EVENT_TABLE])
        assert payload["truncated"] is False
        assert payload["events_seen"] == 1

    def test_the_summary_is_deterministic(self):
        """Same events, same numbers — no clock read inside.

        `computed_at` is stamped by the writer, not by the arithmetic, so two runs
        over one sitting cannot disagree.
        """
        events = [violation(away=3), violation(kind="focus_lost", away=40)]
        assert A.summarize(events, sources=["violation_logs"]) == \
            A.summarize(events, sources=["violation_logs"])
        assert "computed_at" not in A.summarize(events, sources=["violation_logs"])


class TestTheTwoClocks:
    """`occurred_at` is the client's reading; `server_at` is ours.

    Both are kept, because the difference between them is the only evidence that a
    device's clock was moved — and a summary that trusted the client clock would
    let a student rewrite when an absence happened.
    """

    def test_the_drift_between_the_clocks_is_recorded(self):
        payload = A.summarize(
            [session(1, occurred="2026-09-24T01:00:00+00:00",
                     server="2026-09-24T01:05:00+00:00")],
            sources=[EVENT_TABLE],
        )
        assert payload["clock_drift_max_ms"] == 300_000
        assert payload["clock_suspect"] is True

    def test_a_clock_inside_the_tolerance_is_not_suspect(self):
        payload = A.summarize(
            [session(1, occurred="2026-09-24T01:00:00+00:00",
                     server="2026-09-24T01:00:30+00:00")],
            sources=[EVENT_TABLE],
        )
        assert payload["clock_drift_max_ms"] == 30_000
        assert payload["clock_suspect"] is False

    def test_a_charged_absence_has_one_clock_and_no_drift_to_report(self):
        """A `violation_logs` row is stamped by the server, so its drift is zero
        and not "unknown": both ends of the comparison are the same reading."""
        payload = A.summarize([violation()], sources=["violation_logs"])
        assert payload["clock_drift_max_ms"] == 0
        assert payload["clock_suspect"] is False

    def test_the_recorded_window_is_the_events_own_span(self):
        payload = A.summarize([
            violation(at="2026-09-24T01:00:00+00:00"),
            violation(at="2026-09-24T01:20:00+00:00"),
        ], sources=["violation_logs"])
        assert payload["first_event_at"].startswith("2026-09-24T01:00:00")
        assert payload["last_event_at"].startswith("2026-09-24T01:20:00")


class TestWhatASourceMakesMeasurable:
    def test_an_event_source_turns_a_metric_from_unknown_into_a_real_zero(self):
        """Once a source reports these kinds, zero means zero.

        This is the difference the new table buys: with session events arriving and
        no sync gap among them, "no sync gap" is a finding — and the summary says
        `0`, not `None`.
        """
        payload = A.summarize([session(1, kind="tab_switch")], sources=[EVENT_TABLE])
        assert payload["sync_gap_count"] == 0
        assert payload["offline_ms"] == 0
        assert payload["answer_change_count"] == 0

    def test_sync_gaps_and_offline_time_are_summed_from_their_own_kinds(self):
        payload = A.summarize([
            session(1, kind="sync_gap", duration_ms=30_000),
            session(2, kind="sync_gap", duration_ms=12_000),
            session(3, kind="went_offline", duration_ms=45_000),
        ], sources=[EVENT_TABLE])
        assert payload["sync_gap_count"] == 2
        assert payload["offline_ms"] == 45_000

    def test_every_kind_is_counted_without_the_table_needing_a_new_column(self):
        payload = A.summarize([
            session(1, kind="tab_switch"), session(2, kind="tab_switch"),
            session(3, kind="focus_lost"),
        ], sources=[EVENT_TABLE])
        assert payload["counts_by_kind"] == {"tab_switch": 2, "focus_lost": 1}

    def test_per_question_dwell_is_unmeasured_until_an_event_names_a_question(self):
        payload = A.summarize([session(1, duration_ms=5_000)], sources=[EVENT_TABLE])
        assert payload["per_question"] is None

        measured = A.summarize(
            [session(1, duration_ms=5_000, question_index=2),
             session(2, duration_ms=7_000, question_index=2)],
            sources=[EVENT_TABLE])
        assert measured["per_question"] == {"2": 12_000}


class TestHowLongTheStudentWasActuallyWorking:
    def test_effective_time_subtracts_measured_absences_from_the_sitting(self):
        payload = A.summarize([violation(away=10), violation(away=20)],
                              sources=["violation_logs"], sitting_ms=600_000)
        assert payload["sitting_ms"] == 600_000
        assert payload["effective_active_ms"] == 570_000

    def test_effective_time_is_unknown_while_an_absence_has_no_length(self):
        """The honest refusal that matters most on this page.

        One absence of unknown length makes the working time unknowable. Reporting
        `sitting - known` would print a number larger than the truth and present it
        as a measurement of the student's effort.
        """
        payload = A.summarize([violation(away=None), violation(away=10)],
                              sources=["violation_logs"], sitting_ms=600_000)
        assert payload["away_unknown_count"] == 1
        assert payload["effective_active_ms"] is None

    def test_effective_time_never_goes_negative(self):
        """A clock that moved, or absences longer than the sitting: clamp at zero
        rather than print a negative duration."""
        payload = A.summarize([violation(away=10_000)], sources=["violation_logs"],
                              sitting_ms=60_000)
        assert payload["effective_active_ms"] == 0

    def test_a_sitting_with_no_stored_stop_has_no_effective_time(self):
        payload = A.summarize([violation(away=10)], sources=["violation_logs"], sitting_ms=None)
        assert payload["effective_active_ms"] is None


# ── 2. the baseline ──────────────────────────────────────────────────────────

class TestTheClassBaseline:
    def summaries(self, **metric):
        return [dict(metric) for _ in range(metric.pop("n", 1))] if False else [metric]

    def test_no_attempts_means_no_baseline_rows(self):
        assert A.baseline_rows([], "e-1", "c-1") == []

    def test_a_metric_nobody_measured_gets_no_row_at_all(self):
        """Not a row of zeros. A baseline of `0` for an unmeasured metric would be
        a comparison the class cannot supply."""
        rows = A.baseline_rows([{"sync_gap_count": None, "away_count": 2}], "e-1", "c-1")
        assert {r["metric"] for r in rows} == {"away_count"}

    def test_the_percentiles_are_the_apps_own_convention(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        summaries = [{"away_count": v} for v in values]
        row = next(r for r in A.baseline_rows(summaries, "e-1", "c-1")
                   if r["metric"] == "away_count")
        expected = percentiles(values)
        for pct in ("p10", "p25", "p50", "p75", "p90"):
            assert row[pct] == expected[pct]
        assert row["median"] == expected["p50"]
        assert row["iqr"] == round(expected["p75"] - expected["p25"], 1)
        assert row["n"] == len(values)

    def test_mad_is_the_median_absolute_deviation(self):
        values = [1, 2, 3, 4, 100]
        row = next(r for r in A.baseline_rows([{"away_count": v} for v in values], "e-1", "c-1")
                   if r["metric"] == "away_count")
        deviations = [abs(v - percentiles(values)["p50"]) for v in values]
        assert row["mad"] == percentiles(deviations)["p50"]

    def test_a_small_class_is_flagged_rather_than_hidden(self):
        """A class of five still gets a baseline; it is marked as a small sample so
        the reader knows a median over five is a median over five."""
        small = A.baseline_rows([{"away_count": v} for v in range(5)], "e-1", "c-1")
        assert small[0]["small_sample"] is True
        assert small[0]["n"] == 5

        enough = A.baseline_rows([{"away_count": v} for v in range(A.MIN_BASELINE_N)],
                                 "e-1", "c-1")
        assert enough[0]["small_sample"] is False

    def test_the_baseline_carries_the_method_that_produced_it(self):
        row = A.baseline_rows([{"away_count": 3}], "e-1", "c-1")[0]
        assert row["method_version"] == A.BASELINE_METHOD_VERSION
        assert row["exam_id"] == "e-1"
        assert row["class_id"] == "c-1"

    def test_every_metric_is_read_from_the_one_list_the_summary_writes(self):
        """The metrics cannot drift from the columns: the list *is* the columns."""
        assert set(A.METRICS) <= set(A.SUMMARY_COLUMNS)


# ── 3. the migration ─────────────────────────────────────────────────────────

def migration_sql() -> str:
    assert MIGRATION.exists(), (
        "supabase/migrations/035_attempt_session_events.sql is missing — the code "
        "will name tables PostgREST answers PGRST205 for"
    )
    return MIGRATION.read_text(encoding="utf-8")


def _table_body(sql: str, table: str) -> str:
    match = re.search(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+" + table + r"\s*\((.*?)\n\);",
        sql, re.S | re.I)
    assert match, f"the migration does not create {table}"
    return match.group(1)


def columns_declared(table: str) -> set[str]:
    body = _table_body(migration_sql(), table)
    names = set()
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        head = line.split()[0].strip(",").strip('"')
        if re.match(r"^(PRIMARY|UNIQUE|FOREIGN|CONSTRAINT|CHECK|EXCLUDE)\b", head, re.I):
            continue
        names.add(head)
    return names


class TestTheMigration:
    def test_it_creates_the_three_tables(self):
        sql = migration_sql()
        for table in (EVENT_TABLE, SUMMARY_TABLE, BASELINE_TABLE):
            assert re.search(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+" + table, sql, re.I), \
                f"{table} is not created"

    def test_every_table_turns_on_rls(self):
        sql = migration_sql()
        for table in (EVENT_TABLE, SUMMARY_TABLE, BASELINE_TABLE):
            assert re.search(r"ALTER\s+TABLE\s+" + table + r"\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
                             sql, re.I), f"{table} has no RLS"

    def test_no_table_is_open_to_an_anonymous_caller(self):
        """The gate refuses `USING (true)` for exactly this reason: the anon key is
        in every page, so a policy that does not name the caller is a public table."""
        sql = migration_sql()
        assert "USING (true)" not in sql and "USING (TRUE)" not in sql
        for table in (EVENT_TABLE, SUMMARY_TABLE, BASELINE_TABLE):
            assert re.search(r"CREATE\s+POLICY\s+\S+\s+ON\s+" + table, sql, re.I), \
                f"{table} has no policy"
            assert re.search(r"auth\.role\(\)\s*=\s*'service_role'", sql), \
                "the policies do not name the service role"

    def test_a_replayed_event_cannot_be_stored_twice(self):
        """`(attempt_id, seq)` unique is what makes the sequence a sequence: the
        same numbered event arrives twice, the second is refused by the database
        rather than trusting the client not to send it."""
        sql = migration_sql()
        assert re.search(r"UNIQUE\s*\(\s*attempt_id\s*,\s*seq\s*\)", sql, re.I)

    def test_one_baseline_row_per_metric(self):
        sql = migration_sql()
        assert re.search(r"UNIQUE\s*\(\s*exam_id\s*,\s*class_id\s*,\s*metric\s*\)", sql, re.I)

    def test_one_summary_row_per_attempt(self):
        sql = migration_sql()
        assert re.search(r"attempt_id\s+UUID\s+NOT\s+NULL\s+UNIQUE", sql, re.I) or \
            re.search(r"UNIQUE\s*\(\s*attempt_id\s*\)", sql, re.I), \
            "attempt_summary would accumulate a row per submit instead of per attempt"

    def test_the_events_die_with_the_attempt(self):
        sql = migration_sql()
        assert re.search(r"REFERENCES\s+submissions\s*\(\s*id\s*\)\s+ON\s+DELETE\s+CASCADE",
                         sql, re.I), "deleting a submission would orphan its events"

    def test_it_is_idempotent(self):
        """Every statement re-runnable: this is applied by hand as often as by the
        runner, and a second run must not be able to fail."""
        sql = migration_sql()
        for m in re.finditer(r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?", sql, re.I):
            assert m.group(1), "a CREATE TABLE without IF NOT EXISTS"
        for m in re.finditer(r"CREATE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?", sql, re.I):
            assert m.group(1), "a CREATE INDEX without IF NOT EXISTS"
        for m in re.finditer(r"CREATE\s+POLICY\s+(\S+)", sql, re.I):
            assert re.search(r"DROP\s+POLICY\s+IF\s+EXISTS\s+" + re.escape(m.group(1)), sql, re.I), \
                f"policy {m.group(1)} is created without a DROP POLICY IF EXISTS"

    def test_it_destroys_nothing(self):
        """A migration that drops a table or a column is not one that can be run
        against the box that holds every school's marks."""
        sql = migration_sql()
        assert not re.search(r"DROP\s+TABLE", sql, re.I)
        assert not re.search(r"DROP\s+COLUMN", sql, re.I)
        # Word-bounded: this migration introduces a `truncated` column, and the
        # unbounded token would flag the column it just declared as destruction.
        assert not re.search(r"\bTRUNCATE\b", sql, re.I)

    def test_it_documents_the_unmeasured_columns(self):
        """A NULL in this table means "nobody measured this", and the column
        comment has to say so or the next reader writes a zero."""
        sql = migration_sql()
        for column in ("sync_gap_count", "offline_ms", "answer_change_count", "per_question"):
            assert re.search(r"COMMENT\s+ON\s+COLUMN\s+" + SUMMARY_TABLE + r"\." + column,
                             sql, re.I), f"{column} has no comment explaining its NULL"


class TestTheCodeAndTheSchemaAgree:
    def test_every_column_the_summary_writes_is_declared_by_the_migration(self):
        """The one direction that matters: a write into a column PostgREST has
        never heard of is refused at runtime, and the page renders empty."""
        declared = columns_declared(SUMMARY_TABLE)
        assert set(A.SUMMARY_COLUMNS) <= declared, (
            "the summary writes columns the migration does not declare: "
            f"{sorted(set(A.SUMMARY_COLUMNS) - declared)}"
        )

    def test_the_summary_columns_include_what_the_dashboard_reads(self):
        columns = set(A.SUMMARY_COLUMNS)
        for needed in ("attempt_id", "exam_id", "student_id", "school_id",
                       "events_seen", "truncated", "away_count", "away_total_ms",
                       "away_max_ms", "away_short_count", "away_long_count",
                       "away_unknown_count", "effective_active_ms", "sitting_ms",
                       "counts_by_kind", "clock_drift_max_ms", "clock_suspect",
                       "sources", "method_version"):
            assert needed in columns, f"{needed} is not part of the summary"


# ── 4. the wiring ────────────────────────────────────────────────────────────

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """A PostgREST-shaped stand-in: it filters, sorts and records writes."""

    def __init__(self, client, table):
        self.client = client
        self.table = table
        self._filters = []
        self._limit = None
        self._order = None
        self._op = None
        self._payload = None
        self._on_conflict = None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._op, self._payload, self._on_conflict = "upsert", payload, on_conflict
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        self.client.calls.append((self.table, self._op, self._payload, self._on_conflict))
        if self._op in ("insert", "update", "upsert"):
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            if self.table == SUMMARY_TABLE and self.client.raise_on_summary:
                raise RuntimeError("attempt_summary is not there yet")
            self.client.written.setdefault(self.table, []).extend(rows)
            return FakeResult(rows)
        rows = list(self.client.rows.get(self.table, []))
        for column, value in self._filters:
            rows = [r for r in rows if r.get(column) == value]
        if self._order:
            column, desc = self._order
            rows.sort(key=lambda r: r.get(column) or "", reverse=bool(desc))
        if self._limit is not None:
            rows = rows[:self._limit]
        return FakeResult(rows)


class FakeClient:
    def __init__(self, rows=None, raise_on_summary=False):
        self.rows = rows or {}
        self.written = {}
        self.calls = []
        self.raise_on_summary = raise_on_summary

    def table(self, name):
        return FakeQuery(self, name)


def sitting_client(raise_on_summary=False):
    """A student, a draft sitting, and the exam it belongs to."""
    return FakeClient(rows={
        "submissions": [{
            "id": "attempt-1", "exam_id": "e-1", "student_id": "u-1", "status": "draft",
            "started_at": "2026-09-24T01:00:00+00:00",
            "exams": {"school_id": "s-1"},
        }],
        EVENT_TABLE: [],
        "violation_logs": [
            violation(away=4), violation(kind="focus_lost", away=None),
        ],
    }, raise_on_summary=raise_on_summary)


class TestRecordingASummaryWhenASittingEnds:
    def test_a_finished_sitting_records_its_summary(self):
        client = sitting_client()
        from app.services.submission_service import finish_sitting

        outcome = finish_sitting(client, "e-1", "u-1", {
            "exam_id": "e-1", "student_id": "u-1", "answers": {}, "status": "submitted",
            "submitted_at": "2026-09-24T01:10:00+00:00",
        })

        assert outcome == "attempt-1"
        written = client.written.get(SUMMARY_TABLE)
        assert written, "submitting a paper wrote no summary"
        row = written[0]
        assert row["attempt_id"] == "attempt-1"
        assert row["exam_id"] == "e-1"
        assert row["student_id"] == "u-1"
        assert row["school_id"] == "s-1"
        assert row["away_count"] == 2
        assert row["away_unknown_count"] == 1
        assert row["method_version"] == A.METHOD_VERSION

    def test_a_summary_that_cannot_be_written_does_not_cost_the_submission(self):
        """The student's paper is the irreplaceable thing. A summary is derived
        data; losing one is a gap in a dashboard, losing the other is a lost exam."""
        client = sitting_client(raise_on_summary=True)
        from app.services.submission_service import finish_sitting

        outcome = finish_sitting(client, "e-1", "u-1", {
            "exam_id": "e-1", "student_id": "u-1", "answers": {}, "status": "submitted",
        })
        assert outcome == "attempt-1"

    def test_an_already_submitted_attempt_is_not_summarised_twice(self):
        client = sitting_client()
        client.rows["submissions"][0]["status"] = "submitted"
        from app.services.submission_service import finish_sitting

        outcome = finish_sitting(client, "e-1", "u-1", {"status": "submitted"})
        assert outcome == "already_submitted"
        assert SUMMARY_TABLE not in client.written


class TestTheBaselineIsWrittenWhenItIsRead:
    def test_a_missing_baseline_is_computed_from_the_summaries_that_exist(self):
        client = FakeClient(rows={
            SUMMARY_TABLE: [
                {"exam_id": "e-1", "student_id": "u-1", "away_count": 2, "sync_gap_count": None},
                {"exam_id": "e-1", "student_id": "u-2", "away_count": 4, "sync_gap_count": None},
            ],
        })
        rows = A.baseline_for(client, "e-1", "c-1")
        assert {r["metric"] for r in rows} == {"away_count"}
        assert client.written.get(BASELINE_TABLE), "a missing baseline was not written"

    def test_a_fresh_baseline_is_not_recomputed(self):
        client = FakeClient(rows={
            BASELINE_TABLE: [{"exam_id": "e-1", "class_id": "c-1", "metric": "away_count",
                              "n": 20, "median": 1.0, "p10": 0.0, "p25": 0.0, "p50": 1.0,
                              "p75": 2.0, "p90": 3.0, "iqr": 2.0, "mad": 1.0,
                              "metric_min": 0.0, "metric_max": 4.0, "small_sample": False,
                              "method_version": A.BASELINE_METHOD_VERSION,
                              "computed_at": A.now_iso()}],
        })
        A.baseline_for(client, "e-1", "c-1")
        assert BASELINE_TABLE not in client.written

    def test_a_baseline_from_another_method_version_is_recomputed(self):
        """Percentiles are only comparable to percentiles from the same rule; a
        stored row from an older rule would silently answer a different question."""
        client = FakeClient(rows={
            BASELINE_TABLE: [{"exam_id": "e-1", "class_id": "c-1", "metric": "away_count",
                              "n": 20, "median": 1.0, "p10": 0.0, "p25": 0.0, "p50": 1.0,
                              "p75": 2.0, "p90": 3.0, "iqr": 2.0, "mad": 1.0,
                              "metric_min": 0.0, "metric_max": 4.0, "small_sample": False,
                              "method_version": "exam-class-baseline/0",
                              "computed_at": A.now_iso()}],
            SUMMARY_TABLE: [{"exam_id": "e-1", "student_id": "u-1", "away_count": 2}],
        })
        A.baseline_for(client, "e-1", "c-1")
        assert BASELINE_TABLE in client.written


def test_the_module_reuses_the_apps_percentile_convention():
    """One method, one place. A second implementation is how the report page's p25
    and the baseline's p25 stop agreeing."""
    source = (ROOT / "app" / "services" / "attempt_summary.py").read_text(encoding="utf-8")
    assert "exam_report import percentiles" in source
    assert not re.search(r"def _percentile|def percentile", source), \
        "the module grows a second percentile implementation"
