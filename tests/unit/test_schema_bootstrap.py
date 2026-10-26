"""A bootstrap has to be runnable, re-runnable, and honest about what it refused.

`supabase/schema.sql` looks like the way to build this database and is not: on an
empty PostgreSQL it refuses 52 of its 102 statements and leaves one table, because
`profiles` references `classes` and `classes` references `profiles` — a cycle, so
no order works. The numbered migrations are no better on their own (they `ALTER`
the base tables far more often than they create them), and the aggregate files
restate the numbered ones. `deploy/schema_bootstrap.py` therefore runs the base
files, then the policy files, then the numbered migrations, **deferring** a
statement whose dependency does not exist yet rather than dropping or aborting it.

These tests hold the loop that makes that work: what is deferred, what is only
recorded as already-there, and what is a finding. None of them needs a database —
the connection is a stub whose failures are the SQLSTATEs the loop decides on — so
the suite stays runnable on the box that has no `.env` and on CI, which has none
by construction.
"""

from __future__ import annotations

import pathlib

import pytest

from deploy import schema_bootstrap as sb

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── the plan ─────────────────────────────────────────────────────────────────

class TestTheOrderIsTheOneThatCanRun:
    def test_the_base_files_run_first_and_in_their_own_order(self):
        plan = sb.plan(REPO)
        sources = []
        for step in plan:
            if not sources or sources[-1] != step.source:
                sources.append(step.source)
        assert sources[:2] == ["supabase/schema.sql", "supabase/_COMPLETE_SETUP.sql"], (
            "the exam core and the school half are the two files everything else "
            "alters, so they are the two that come first:\n    " + "\n    ".join(sources[:4]))

    def test_the_numbered_migrations_keep_name_order(self):
        plan = sb.plan(REPO)
        migrations = []
        for step in plan:
            if step.source.startswith("supabase/migrations/") and step.source not in migrations:
                migrations.append(step.source)
        assert migrations == sorted(migrations), (
            "the numbered files are applied in name order; a set reordered here is a "
            "bootstrap that stops matching what the ledger has recorded")
        assert len(migrations) > 70, "fewer migrations than this repository carries"

    def test_the_plan_is_not_empty_and_not_a_handful(self):
        """A guard that read nothing would report exactly what a clean plan reports."""
        plan = sb.plan(REPO)
        assert len(plan) > 1000, (
            f"only {len(plan)} statement(s) planned — the two base files alone carry "
            "more than that")


class TestTheTrapFilesAreLeftOutByName:
    def test_the_aggregate_is_not_applied_on_top_of_what_it_aggregates(self):
        sources = {step.source for step in sb.plan(REPO)}
        assert "supabase/migrations/_RUN_ALL_AT_ONCE.sql" not in sources
        assert not any("/combined_" in source for source in sources)

    def test_the_placeholder_is_not_offered_as_a_base(self):
        sources = {step.source for step in sb.plan(REPO)}
        assert "supabase/migrations/001_initial_schema.sql" not in sources

    def test_every_exclusion_says_why(self):
        assert sb.EXCLUDED, "the skip list is empty, so it is not a decision"
        for name, reason in sb.EXCLUDED.items():
            assert reason.strip(), f"{name} is skipped with no reason attached"
        # And the names it holds are ones this repository really carries, so a
        # rename cannot leave the list describing nothing. The keys are lower-cased
        # because that is how a path is matched here, which is also why the files
        # are looked for that way.
        carried = {(REPO / "supabase" / "migrations" / path.name).name.lower()
                   for path in (REPO / "supabase" / "migrations").glob("*.sql")}
        for name in sb.EXCLUDED:
            assert name in carried, (
                f"{name} is excluded by name but no file in supabase/migrations "
                "answers to it")

    def test_the_placeholder_really_creates_nothing(self):
        """The reason written for it has to stay true."""
        text = (REPO / "supabase" / "migrations" / "001_initial_schema.sql").read_text()
        assert not sb.statements(text), (
            "001_initial_schema.sql now carries statements — it is no longer the "
            "placeholder the exclusion list describes")


# ── splitting, for execution rather than for reading ─────────────────────────

class TestTheSplitterKeepsWhatItSplits:
    def test_a_dollar_body_stays_in_one_statement(self):
        text = (
            "CREATE OR REPLACE FUNCTION f() RETURNS TRIGGER AS $$\n"
            "BEGIN\n  NEW.a := 1;\n  RETURN NEW;\nEND;\n$$ LANGUAGE plpgsql;\n"
            "SELECT 1;\n")
        parts = sb.statements(text)
        assert len(parts) == 2, parts
        assert "NEW.a := 1;" in parts[0], (
            "a `;` inside a `$$` body ended the statement, so the function would be "
            "sent to the server as two fragments")

    def test_a_tagged_dollar_body_is_handled(self):
        parts = sb.statements("DO $body$ BEGIN PERFORM 1; END $body$;\nSELECT 2;")
        assert len(parts) == 2

    def test_a_semicolon_in_a_string_is_not_a_separator(self):
        parts = sb.statements("INSERT INTO t VALUES ('a;b');\nSELECT 1;")
        assert len(parts) == 2
        assert "a;b" in parts[0]

    def test_a_semicolon_in_a_comment_is_not_a_separator(self):
        parts = sb.statements("-- one; two;\nSELECT 1;")
        assert len(parts) == 1, parts

    def test_an_empty_or_comment_only_file_yields_nothing(self):
        assert sb.statements("") == []
        assert sb.statements("-- nothing here\n") == []


# ── the loop ─────────────────────────────────────────────────────────────────

class PgError(Exception):
    def __init__(self, pgcode, message="boom"):
        super().__init__(message)
        self.pgcode = pgcode


class StubCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql):
        self.conn.executed.append(sql)
        behaviour = self.conn.behaviour.get(sql)
        if callable(behaviour):
            behaviour()                     # may raise, exactly as a server would
        elif behaviour is not None:
            raise behaviour

    def close(self):
        return None


class StubConnection:
    """Enough of a connection to decide what the loop does, and no server."""

    def __init__(self, behaviour=None):
        self.behaviour = behaviour or {}
        self.executed: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return StubCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _step(sql, source="m.sql"):
    return sb.Step(source, sql)


class TestTheLoopDecidesBySqlstate:
    def test_a_missing_dependency_is_deferred_not_dropped(self):
        # `b` does not exist on the first attempt; the run has to come back to the
        # statement that needs it once it does.
        created: set[str] = set()

        def make_b():
            created.add("b")

        def make_a():
            if "b" not in created:
                raise PgError("42P01", 'relation "b" does not exist')
            created.add("a")

        conn = StubConnection({"CREATE TABLE b (id int)": make_b,
                               "CREATE TABLE a (id int, b_id int REFERENCES b(id))": make_a})
        # The dependent statement comes *first*, which is the shape this
        # repository really has: `profiles` names `classes` eight lines before
        # `classes` is created, and `classes` names `profiles` back.
        report = sb.apply(conn, [_step("CREATE TABLE a (id int, b_id int REFERENCES b(id))"),
                                 _step("CREATE TABLE b (id int)")])
        assert report.applied == 2, (
            "a statement whose dependency arrives later in the same run must be "
            "deferred and re-run, not skipped")
        assert created == {"a", "b"}
        assert not report.pending and not report.hard
        assert report.passes >= 2

    def test_an_already_there_error_is_counted_not_failed(self):
        conn = StubConnection({"CREATE POLICY p ON t": PgError("42710", "already exists")})
        report = sb.apply(conn, [_step("CREATE POLICY p ON t")])
        assert report.hard == []
        assert report.tolerated == {"42710": 1}
        assert report.tolerated_total == 1
        assert report.converged, "a policy that already exists is a re-run, not a defect"

    def test_a_real_error_ends_up_a_finding_that_names_its_statement(self):
        """It gets the sweep's one extra attempt, and it is the finding afterwards."""
        conn = StubConnection({"UPDATE c SET s = 1": PgError("42804", "type mismatch")})
        report = sb.apply(conn, [_step("UPDATE c SET s = 1", "007_x.sql")])
        assert report.converged is False
        step, code, message = report.hard[0]
        assert step.source == "007_x.sql" and code == "42804"
        assert "type mismatch" in message
        assert step.head().startswith("UPDATE c")
        assert report.pending == [], (
            "a type conflict is not a missing dependency: it is reported as refused, "
            "not as something the next pass is expected to fix")

    def test_no_progress_ends_the_run_instead_of_looping(self):
        conn = StubConnection({"CREATE TABLE a": PgError("42P01", "missing")})
        report = sb.apply(conn, [_step("CREATE TABLE a")], max_passes=50)
        assert report.passes == 1, "an unproductive pass must end the loop"
        assert len(report.pending) == 1

    def test_the_passes_are_bounded(self):
        """A chain deeper than the bound ends with what it could not reach.

        The run has to answer "this is still waiting", not loop until a timeout —
        the difference between a measurement and a hang.
        """
        reached: dict[str, int] = {}

        def chain(depth):
            def attempt():
                reached[depth] = reached.get(depth, 0) + 1
                if reached[depth] < depth:
                    raise PgError("42P01", "still missing")
            return attempt

        steps = [_step(f"SELECT {n}") for n in range(1, 6)]
        conn = StubConnection({step.sql: chain(n) for n, step in enumerate(steps, 1)})
        report = sb.apply(conn, steps, max_passes=2)
        assert report.passes <= 2
        assert report.pending, "a chain deeper than the bound leaves work unreached"

    def test_a_failed_statement_rolls_back_and_a_good_one_commits(self):
        conn = StubConnection({"BAD": PgError("42P01", "missing")})
        sb.apply(conn, [_step("BAD"), _step("GOOD")], max_passes=1)
        assert conn.rollbacks == 1
        assert conn.commits == 1


# ── the measurement ──────────────────────────────────────────────────────────

class TestTheMeasurementCountsViews:
    def test_tables_reads_views_as_well_as_tables(self):
        seen = {}

        class Cur:
            def execute(self, sql):
                seen["sql"] = sql

            def fetchall(self):
                return [("classes",)]

        assert sb.tables(Cur()) == {"classes"}
        assert "pg_views" in seen["sql"], (
            "two of the names the SQL declares are views; reading pg_tables alone "
            "would call a view a missing table")

    def test_the_legacy_name_is_explained_and_not_a_silent_pass(self):
        assert "activation_codes" in sb.LEGACY
        assert sb.LEGACY["activation_codes"].strip(), "a legacy name with no reason"
        text = (REPO / "supabase" / "migrations" /
                "20260608_fix_rls_policies.sql").read_text(encoding="utf-8")
        assert "legacy name" in text, (
            "the exemption says the repository calls activation_codes a legacy name; "
            "the file that says so no longer does")


# ── the target ───────────────────────────────────────────────────────────────

#: The fixture password. Assembled into a URL by :func:`pooler` rather than written
#: in one: a literal `user:password@host` is exactly what a secret scanner refuses,
#: and this file would then be the defect its own subject is about.
#: `tests/unit/test_safe_url.py` writes its fixtures the same way.
SECRET = "hunter2"


def pooler(ref, password=SECRET, host="aws-0-ap-south-1.pooler.supabase.com"):
    """A session-mode pooler DSN for `ref`, built rather than typed."""
    return f"postgresql://postgres.{ref}:{password}@{host}:5432/postgres"


class TestProductionIsRefusedByRefNotByHost:
    def test_the_production_project_is_refused(self):
        from deploy import supabase_region
        refusal = sb.refuse_production(pooler(supabase_region.REF))
        assert refusal and "refusing" in refusal
        assert supabase_region.REF in refusal

    def test_a_staging_project_is_allowed(self):
        # A synthetic ref, not the real staging one: this fixture is about whether a
        # ref other than production's is let through, and the real ref belongs in
        # the deployment record rather than in a test.
        assert sb.refuse_production(pooler("abcdefghijklmnopqrst")) is None

    def test_the_dashboard_placeholder_is_refused(self):
        url = ("postgresql://postgres:[YOUR-PASSWORD]@db.abcdefghijklmnopqrst"
               ".supabase.co:5432/postgres")
        refusal = sb.refuse_production(url)
        assert refusal and "placeholder" in refusal

    def test_the_refusal_never_prints_the_password(self):
        from deploy import supabase_region
        refusal = sb.refuse_production(pooler(supabase_region.REF)) or ""
        assert refusal, "the production URL must be refused, not returned clean"
        assert SECRET not in refusal
        assert "@" not in refusal, (
            "a refusal that still carries the authority part has printed the URL it "
            "was refusing")


# ── the exit codes, through main() ───────────────────────────────────────────

class TestTheExitCodes:
    def test_plan_touches_no_server_and_succeeds(self, capsys):
        code = sb.main(["--repo", str(REPO), "--plan"])
        out = capsys.readouterr().out
        assert code == 0
        assert "statement(s)" in out
        assert "left out:" in out, "the plan has to name what it is not doing"

    def test_no_url_is_cannot_measure_not_a_pass(self, monkeypatch, capsys):
        monkeypatch.delenv("DIRECT_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(sb, "load_url", lambda repo: (_ for _ in ()).throw(
            SystemExit("no DIRECT_URL")))
        code = sb.main(["--repo", str(REPO)])
        assert code == 2, "a run with no target measured nothing and must not pass"
        assert "cannot measure" in capsys.readouterr().err

    def test_a_production_url_exits_1_without_connecting(self, monkeypatch, capsys):
        from deploy import supabase_region

        def explode(url):
            raise AssertionError("connected to a database it had already refused")

        monkeypatch.setattr(sb, "apply", explode)
        code = sb.main(["--repo", str(REPO), "--url", pooler(supabase_region.REF)])
        assert code == 1
        err = capsys.readouterr().err
        assert "refusing" in err and supabase_region.REF in err
        assert SECRET not in err, "the refusal printed the URL it was refusing"

    def test_no_sql_files_is_cannot_measure(self, tmp_path, capsys):
        code = sb.main(["--repo", str(tmp_path), "--plan"])
        assert code == 2
        assert "cannot measure" in capsys.readouterr().err


# ── the audit, which must write nothing ─────────────────────────────────────

class AuditCursor:
    """Answers the two reads `audit()` makes, and records everything it is asked."""

    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows
        self.statements: list[str] = []
        self._result: list[tuple] = []

    def execute(self, sql):
        self.statements.append(sql)
        flat = " ".join(sql.split()).lower()
        if "information_schema.columns" in flat:
            self._result = [(table, column)
                            for table, cols in self.columns.items()
                            for column in cols]
        elif "pg_class" in flat:
            self._result = [(name, count) for name, count in self.rows.items()]
        else:                                   # pragma: no cover - a new query
            raise AssertionError(f"the audit issued a statement it should not: {sql}")

    def fetchall(self):
        return self._result

    def close(self):
        return None


WANTED = {"schools": {"id", "name", "npsn"}, "profiles": {"id", "full_name"},
          "exams": {"id", "title", "answer_key"}}


class TestTheAuditSeparatesTheFourCases:
    def test_absent_short_and_untouched_are_told_apart(self):
        cur = AuditCursor(
            columns={"schools": {"id", "name", "npsn"},       # complete
                     "profiles": {"id"},                       # short of full_name
                     "auth_user_legacy": {"id", "username"}},  # not declared
            rows={"schools": 12, "profiles": 3, "auth_user_legacy": 900})
        reading = sb.audit(cur, WANTED)
        assert reading["absent"] == {"exams": {"id", "title", "answer_key"}}
        assert reading["short"] == {"profiles": ["full_name"]}
        assert reading["foreign"] == ["auth_user_legacy"]
        assert reading["rows"] == {"profiles": 3}, (
            "a table the bootstrap would ALTER is reported with how many rows it "
            "already holds, so nobody reads a populated table as scaffolding")

    def test_a_table_with_more_columns_than_declared_is_not_a_finding(self):
        """The normal case: migrations add columns with IF NOT EXISTS."""
        cur = AuditCursor(columns={"schools": {"id", "name", "npsn", "legacy_extra"},
                                   "profiles": {"id", "full_name"},
                                   "exams": {"id", "title", "answer_key"}},
                          rows={})
        reading = sb.audit(cur, WANTED)
        assert not reading["absent"] and not reading["short"]
        assert reading["foreign"] == []

    def test_a_legacy_name_nothing_creates_is_not_a_gap(self):
        cur = AuditCursor(columns={}, rows={})
        reading = sb.audit(cur, {"activation_codes": {"id"}})
        assert reading["absent"] == {}, (
            "the repository says nothing creates activation_codes; an audit that "
            "demanded it would fail a target that is exactly right")
        assert reading["legacy"] == ["activation_codes"]

    def test_the_audit_issues_only_reads(self):
        cur = AuditCursor(columns={"schools": {"id", "name", "npsn"}}, rows={})
        sb.audit(cur, WANTED)
        assert cur.statements, "the audit read nothing, so it measured nothing"
        for sql in cur.statements:
            assert sql.strip().lower().startswith("select"), (
                f"the audit is documented as writing nothing and issued: {sql}")


class TestTheAuditThroughMain:
    def test_a_production_url_is_refused_before_anything_is_read(self, monkeypatch, capsys):
        from deploy import supabase_region

        def explode(url):
            raise AssertionError("read a database it had already refused")

        monkeypatch.setattr(sb, "_audit", explode)
        code = sb.main(["--repo", str(REPO), "--audit",
                        "--url", pooler(supabase_region.REF)])
        assert code == 1
        assert "refusing" in capsys.readouterr().err

    def test_no_url_is_cannot_measure(self, monkeypatch, capsys):
        monkeypatch.setattr(sb, "load_url", lambda repo: (_ for _ in ()).throw(
            SystemExit("no DIRECT_URL")))
        code = sb.main(["--repo", str(REPO), "--audit"])
        assert code == 2
        assert "cannot measure" in capsys.readouterr().err


# ── the repair sweep ────────────────────────────────────────────────────────

class TestARefusedStatementGetsOneMoreAttempt:
    def test_a_shape_conflict_is_repaired_after_a_later_migration(self):
        """`007` fails while `classes.school_id` is integer, and `076` sorts after it.

        The sweep is what turns "eight RLS policies are missing" into "the repair
        migration ran and the statements that tripped on the shape then applied".
        """
        attempts: dict[str, int] = {}

        def fails_once():
            arrived = attempts.get("u", 0) + 1
            attempts["u"] = arrived
            if arrived == 1:
                raise PgError("42804", "column is of type integer but expression is of type uuid")

        conn = StubConnection({"UPDATE classes SET school_id = 'x'": fails_once})
        report = sb.apply(conn, [_step("UPDATE classes SET school_id = 'x'", "007_x.sql"),
                                 _step("SELECT 1", "076_y.sql")])
        assert report.applied == 2
        assert report.repaired == 1, (
            "the statement that failed on the shape must be retried once after the "
            "plan, and the run must say that it was")
        assert report.hard == [] and report.converged

    def test_what_still_fails_after_the_sweep_is_the_finding(self):
        conn = StubConnection({"UPDATE c SET s = 1": PgError("42804", "still wrong")})
        report = sb.apply(conn, [_step("UPDATE c SET s = 1", "007_x.sql")])
        assert len(report.hard) == 1
        assert report.repaired == 0
        step, code, message = report.hard[0]
        assert step.source == "007_x.sql" and code == "42804" and "still wrong" in message

    def test_the_sweep_does_not_run_for_ever(self):
        attempts = {"n": 0}

        def always():
            attempts["n"] += 1
            raise PgError("42804", "wrong")

        conn = StubConnection({"UPDATE c SET s = 1": always})
        sb.apply(conn, [_step("UPDATE c SET s = 1")])
        assert attempts["n"] == 2, (
            "a statement is tried once in the plan and once on the sweep — not "
            "once per pass")


class TestADuplicateSeedIsNotAFailure:
    def test_a_seed_insert_of_a_row_that_exists_is_counted(self):
        conn = StubConnection({"INSERT INTO schools (id) VALUES ('x')":
                               PgError("23505", 'duplicate key value violates unique '
                                                'constraint "schools_pkey"')})
        report = sb.apply(conn, [_step("INSERT INTO schools (id) VALUES ('x')", "007_x.sql")])
        assert report.hard == []
        assert report.tolerated == {"23505": 1}

    def test_a_duplicate_key_that_is_not_a_seed_is_still_a_finding(self):
        """Only an INSERT of a fixed row counts: an UPDATE hitting 23505 is a defect."""
        conn = StubConnection({"UPDATE schools SET id = 'x'": PgError("23505", "duplicate")})
        report = sb.apply(conn, [_step("UPDATE schools SET id = 'x'")])
        assert len(report.hard) == 1


# ── the target, refused by default ──────────────────────────────────────────

class TestTheDefaultIsNo:
    def test_the_live_domain_is_refused_even_without_a_ref(self):
        refusal = sb.refuse_production(
            "postgresql://postgres:pw@db.scangrade.web.id:5432/postgres")
        assert refusal and sb.LIVE_DOMAIN in refusal

    def test_an_unidentifiable_remote_target_is_refused(self):
        """Silence is not evidence of safety: no ref, not local, so not ours."""
        refusal = sb.refuse_production("postgresql://postgres:pw@10.0.0.9:5432/other")
        assert refusal and "no Supabase project ref" in refusal

    def test_the_escape_hatch_is_explicit(self):
        url = "postgresql://postgres:pw@10.0.0.9:5432/other"
        assert sb.refuse_production(url, allow_unidentified=True) is None

    def test_a_local_socket_needs_no_ref(self):
        assert sb.refuse_production("postgresql://postgres:@/postgres?host=/tmp/pg") is None
        assert sb.refuse_production("postgresql://postgres:pw@localhost:5432/postgres") is None


# ── what was actually built ─────────────────────────────────────────────────

class ShapeCursor:
    """The four reads `verify()` makes, in order."""

    def __init__(self, results):
        self.results = list(results)
        self.statements: list[str] = []
        self._current = None

    def execute(self, sql):
        self.statements.append(sql)
        self._current = self.results.pop(0)

    def fetchone(self):
        return self._current


class TestTheShapeIsCheckedNotAssumed:
    def test_foreign_keys_rls_and_policies_are_counted(self):
        cur = ShapeCursor([(191,), (0,), (76, 89), (219,)])
        shape = sb.verify(cur)
        assert shape == {"foreign_keys": 191, "not_valid": 0,
                         "rls_tables": 76, "tables": 89, "policies": 219}
        assert len(cur.statements) == 4

    def test_a_not_valid_foreign_key_is_asked_for_by_name(self):
        """`NOT VALID` is a constraint that exists without being enforced."""
        cur = ShapeCursor([(191,), (3,), (76, 89), (219,)])
        shape = sb.verify(cur)
        assert shape["not_valid"] == 3
        assert any("convalidated" in sql for sql in cur.statements), (
            "the count of unenforced foreign keys has to come from the catalogue "
            "flag that means it, not from a total")


# ── migration 076, and the history it must not rewrite ──────────────────────

MIGRATIONS = REPO / "supabase" / "migrations"


class TestTheRepairIsAdditive:
    def test_076_exists_and_plans_last_among_the_numbered_files(self):
        plan = sb.plan(REPO)
        sources = [step.source for step in plan]
        assert "supabase/migrations/076_classes_school_id_uuid.sql" in sources
        numbered = [s for s in dict.fromkeys(sources)
                    if s.startswith("supabase/migrations/0") and s.split("/")[-1][:3].isdigit()]
        assert numbered[-1].endswith("076_classes_school_id_uuid.sql"), (
            "a repair migration has to run after the files whose shape it repairs:\n    "
            + "\n    ".join(numbered[-4:]))

    def test_003_is_untouched_so_its_digest_still_matches_a_ledger_record(self):
        """The repair is a new file; editing an applied one is the false alarm."""
        text = (MIGRATIONS / "003_submission_hidden_retracted.sql").read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1" in text, (
            "003's bytes are what a box's ledger recorded; if this assertion fails "
            "because the line was edited away, the repair took the path that "
            "reports APPLIED, THEN EDITED for a file nobody edited")

    def test_076_refuses_to_invent_a_school_for_a_row_it_cannot_map(self):
        text = (MIGRATIONS / "076_classes_school_id_uuid.sql").read_text(encoding="utf-8")
        assert "RAISE EXCEPTION" in text and "populated > 0" in text, (
            "an integer that is already a value cannot be mapped to a schools(id) "
            "uuid; the migration must stop rather than guess")
        assert "DROP DEFAULT" in text, (
            "the `DEFAULT 1` is the reason every class belonged to school 1")

    def test_076_is_safe_to_re_run(self):
        text = (MIGRATIONS / "076_classes_school_id_uuid.sql").read_text(encoding="utf-8")
        assert "IF col_type IS NULL THEN" in text and "RETURN;" in text, (
            "a second run must return without touching anything")
        assert "col_type = 'integer'" in text, (
            "the retype must be conditional on the column still being integer")
        assert "DROP CONSTRAINT IF EXISTS classes_school_id_fkey" in text

    def test_the_declared_table_that_nothing_creates_is_still_explained(self):
        """92 declared = 91 relations + one name; the report has to say which."""
        from deploy import schema_contract
        declared = schema_contract.schema_from_migrations()
        assert "activation_codes" in declared
        assert declared["activation_codes"] == set(), (
            "activation_codes is declared with no columns at all — it is a name in "
            "guarded statements, not a table anything defines")


class TestAViewThatCannotShrinkIsNotAFailure:
    """`CREATE OR REPLACE VIEW` adds columns and cannot remove them.

    The second run of a bootstrap meets the view a later file widened, and
    Postgres answers `cannot drop columns from view` (42P16). Refusing there would
    make a bootstrap runnable exactly once; dropping the view to replay the
    narrower definition would take the later file's columns away until it ran.
    """

    def test_a_narrower_replace_of_a_wider_view_is_counted(self):
        conn = StubConnection({"CREATE OR REPLACE VIEW active_school_years (id) AS SELECT 1":
                               PgError("42P16", "cannot drop columns from view")})
        report = sb.apply(conn, [_step(
            "CREATE OR REPLACE VIEW active_school_years (id) AS SELECT 1")])
        assert report.hard == []
        assert report.tolerated == {"42P16": 1}

    def test_the_same_code_on_anything_else_is_still_a_finding(self):
        conn = StubConnection({"ALTER TABLE t ADD COLUMN a int": PgError("42P16", "other")})
        report = sb.apply(conn, [_step("ALTER TABLE t ADD COLUMN a int")])
        assert len(report.hard) == 1, (
            "42P16 is tolerated only where it means 'the view is already wider'; "
            "anywhere else it is a shape the release did not expect")

    def test_the_recogniser_reads_the_statement_not_the_message(self):
        assert sb._is_view_replace("CREATE OR REPLACE VIEW v (id) AS SELECT 1")
        assert sb._is_view_replace("  create or replace view v (id) as select 1")
        assert not sb._is_view_replace("CREATE VIEW v AS SELECT 1")
        assert not sb._is_view_replace("SELECT * FROM v")


class TestThePlanOnlyAdds:
    """A bootstrap adds. The one place a `DROP TABLE` would be unrecoverable is
    the target this tool exists to be pointed at: a database holding somebody
    else's data."""

    def test_no_step_drops_a_relation_or_truncates(self):
        offenders = sb.destructive(sb.plan(REPO))
        assert not offenders, (
            "the plan removes a relation, which is not recoverable on a target that "
            "already holds data:\n    "
            + "\n    ".join(f"{s.source}: {s.head()}" for s in offenders))

    def test_the_detector_would_recognise_one(self):
        """A guard that finds nothing has to be shown able to find something."""
        for sql in ("DROP TABLE legacy_users", "drop schema if exists old cascade",
                    "TRUNCATE public.exams"):
            assert sb.destructive([sb.Step("x.sql", sql)]), sql
        for sql in ("DELETE FROM trial_settings WHERE id <> 1",
                    "ALTER TABLE t DROP COLUMN IF EXISTS staged",
                    "DROP POLICY IF EXISTS p ON t"):
            assert not sb.destructive([sb.Step("x.sql", sql)]), sql

    def test_the_row_deletes_that_exist_are_housekeeping_or_a_function_body(self):
        """Two `DELETE FROM` clauses are in the plan, and neither runs on apply.

        034 clears a singleton table this same plan creates (`trial_settings`),
        and 065's `DELETE` is inside a `CREATE OR REPLACE FUNCTION` body — a
        statement the bootstrap creates rather than executes. A future third one
        has to be accounted for here rather than discovered on a customer's box.
        """
        deletes = [s for s in sb.plan(REPO) if sb.ROW_DELETE.search(s.sql)]
        assert deletes, "the count printed by --plan is for statements that exist"
        for step in deletes:
            in_function = step.sql.lstrip().upper().startswith("CREATE OR REPLACE FUNCTION")
            assert in_function or "trial_settings" in step.sql, (
                f"{step.source} deletes rows and is neither the singleton cleanup "
                f"nor a function body: {step.head()}")
