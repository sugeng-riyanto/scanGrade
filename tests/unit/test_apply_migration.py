"""Guards for the tool that trials a migration before anyone trusts it.

The value of ``deploy/apply_migration.py`` rests on a few properties, and each
one is here because getting it wrong is silent rather than loud:

* **The rollback is verified, not assumed.** The tool's whole promise is that the
  SQL ran for real and left nothing behind. If it merely *claimed* that, a
  migration would be applied to production by a dry run — the worst outcome
  available. So a mismatch after rollback must be an error.
* **A file that commits by itself is refused.** ``COMMIT`` inside the migration
  hands the transaction to the file, and a rollback afterwards undoes nothing.
* **A statement that cannot run in a transaction is refused**, because a dry run
  of it would be a lie rather than a test.
* **The target is identified.** DDL applied to the wrong project cannot be undone
  by re-running the tool.
* **There is no way to skip the trial.** ``--commit`` runs it first, in the same
  invocation, so nothing has to be remembered under time pressure.
"""
import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest

from tests.unit.git_env import git_env

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
REF = "roshkbzgfzpfedowozfo"
OTHER = "zzzzzzzzzzzzzzzzzzzz"


def _load():
    if str(DEPLOY) not in sys.path:
        sys.path.insert(0, str(DEPLOY))
    spec = importlib.util.spec_from_file_location("apply_migration",
                                                  DEPLOY / "apply_migration.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["apply_migration"] = module
    spec.loader.exec_module(module)
    return module


app_mig = _load()
SOURCE = (DEPLOY / "apply_migration.py").read_text(encoding="utf-8")


# ── fakes ────────────────────────────────────────────────────────────────────

class _Info:
    def __init__(self, status):
        self.transaction_status = status


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, *a):
        self.conn.executed.append(sql)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    """Just enough of psycopg2's connection for the paths under test."""

    def __init__(self, status=None):
        self.executed = []
        self.notices = []
        self.info = _Info(status if status is not None
                          else psycopg2.extensions.TRANSACTION_STATUS_INTRANS)
        self.rolled_back = False
        self.committed = False
        self.closed = False

    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


# ── reading the target out of a URL ──────────────────────────────────────────

def test_the_ref_is_read_before_the_password_not_after():
    """The pooler username is `postgres.<ref>` and the password follows it, so the
    ref is terminated by `:` — matching on `@` refused every real URL."""
    url = f"postgresql://postgres.{REF}:s3cret@aws-1-ap-south-1.pooler.supabase.com:5432/postgres"

    assert app_mig.project_ref(url) == REF


def test_the_ref_is_read_when_the_password_is_percent_encoded():
    url = f"postgresql://postgres.{REF}:p%40ss%3Aword@aws-1.pooler.supabase.com:5432/postgres"

    assert app_mig.project_ref(url) == REF


def test_the_ref_is_read_from_the_direct_host():
    assert app_mig.project_ref(f"postgresql://postgres:pw@db.{REF}.supabase.co:5432/postgres") == REF


def test_the_ref_is_read_from_the_api_url():
    assert app_mig.project_ref(f"https://{REF}.supabase.co") == REF


def test_an_unrelated_url_has_no_ref():
    assert app_mig.project_ref("postgresql://localhost:5432/scangrade") is None


# ── refusing the wrong database ──────────────────────────────────────────────

def test_a_mismatched_target_is_refused(monkeypatch):
    monkeypatch.setattr(app_mig.db_snapshot, "load_credentials",
                        lambda repo: (f"https://{OTHER}.supabase.co", "key"))

    with pytest.raises(SystemExit) as exc:
        app_mig.check_target(f"postgresql://postgres.{REF}:pw@h.pooler.supabase.com:5432/postgres",
                             Path("."))

    assert REF in str(exc.value) and OTHER in str(exc.value)


def test_a_matching_target_is_accepted(monkeypatch):
    monkeypatch.setattr(app_mig.db_snapshot, "load_credentials",
                        lambda repo: (f"https://{REF}.supabase.co", "key"))

    assert app_mig.check_target(
        f"postgresql://postgres.{REF}:pw@h.pooler.supabase.com:5432/postgres",
        Path(".")) == REF


def test_an_unidentifiable_target_is_refused(monkeypatch):
    monkeypatch.setattr(app_mig.db_snapshot, "load_credentials",
                        lambda repo: (f"https://{REF}.supabase.co", "key"))

    with pytest.raises(SystemExit):
        app_mig.check_target("postgresql://localhost/scangrade", Path("."))


def test_the_dashboard_placeholder_is_never_used(monkeypatch, tmp_path):
    monkeypatch.setenv("DIRECT_URL",
                       f"postgresql://postgres.{REF}:[YOUR-PASSWORD]@h.pooler.supabase.com:5432/postgres")

    with pytest.raises(SystemExit) as exc:
        app_mig.load_migration_url(tmp_path)

    assert "YOUR-PASSWORD" in str(exc.value)


def test_no_credential_at_all_is_an_error(monkeypatch, tmp_path):
    monkeypatch.delenv("DIRECT_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SystemExit):
        app_mig.load_migration_url(tmp_path)


# ── the rollback is verified, not assumed ────────────────────────────────────

def _run_dry_run(monkeypatch, before, mid, after, tmp_path=None):
    """Drive dry_run with scripted schema reads and a no-op trial."""
    reads = iter([before, mid, after])
    monkeypatch.setattr(app_mig, "connect", lambda url: FakeConn())
    monkeypatch.setattr(app_mig, "schema_snapshot", lambda cur: next(reads))
    monkeypatch.setattr(app_mig, "trial", lambda conn, sql, require: None)
    return app_mig.dry_run("postgresql://x", "SELECT 1;", False)


def test_a_clean_rollback_reports_the_delta(monkeypatch):
    before = {"column public.a.b": "text null=YES"}
    mid = dict(before, **{"column public.a.c": "text null=YES"})

    delta = _run_dry_run(monkeypatch, before, mid, dict(before))

    assert any("+ column public.a.c" in line for line in delta)
    assert app_mig.count_changes(delta) == 1


def test_a_leaked_change_after_rollback_is_fatal(monkeypatch):
    """If the schema differs after ROLLBACK, the dry run lied. It must not pass."""
    before = {"column public.a.b": "text null=YES"}
    mid = dict(before, **{"column public.a.c": "text null=YES"})

    with pytest.raises(SystemExit) as exc:
        _run_dry_run(monkeypatch, before, mid, mid)      # `after` still has the change

    assert exc.value.code == 1


def test_a_migration_that_commits_itself_is_refused():
    """Reading the connection's transaction status after execution is what makes
    this reliable — matching the text `COMMIT` would trip over `DO $$ BEGIN`."""
    conn = FakeConn(status=psycopg2.extensions.TRANSACTION_STATUS_IDLE)

    with pytest.raises(app_mig.TransactionEscaped) as exc:
        app_mig.execute_migration(conn, "SELECT 1; COMMIT;", "first pass")

    assert "left the transaction" in str(exc.value)


def test_a_still_open_transaction_is_accepted():
    conn = FakeConn(status=psycopg2.extensions.TRANSACTION_STATUS_INTRANS)

    app_mig.execute_migration(conn, "SELECT 1;", "first pass")

    assert conn.executed == ["SELECT 1;"]


def test_a_file_needing_a_non_transactional_statement_is_refused(monkeypatch):
    reads = iter([{}, {}, {}])
    monkeypatch.setattr(app_mig, "connect", lambda url: FakeConn())
    monkeypatch.setattr(app_mig, "schema_snapshot", lambda cur: next(reads))

    def refuse(conn, sql, require):
        raise psycopg2.errors.ActiveSqlTransaction(
            "CREATE INDEX CONCURRENTLY cannot run inside a transaction block")

    monkeypatch.setattr(app_mig, "trial", refuse)

    with pytest.raises(SystemExit) as exc:
        app_mig.dry_run("postgresql://x", "CREATE INDEX CONCURRENTLY ...", False)

    assert exc.value.code == 4


def test_transaction_refusals_are_recognised_by_code_or_by_text():
    class ByCode(Exception):
        pgcode = "25001"

    assert app_mig.is_transaction_refusal(ByCode("anything"))
    assert app_mig.is_transaction_refusal(
        Exception("CREATE INDEX CONCURRENTLY cannot run inside a transaction block"))
    assert not app_mig.is_transaction_refusal(Exception("relation does not exist"))


# ── the re-run is reported, not fatal, unless asked ──────────────────────────

def test_a_non_idempotent_file_is_reported_but_not_fatal(monkeypatch, capsys):
    calls = {"n": 0}

    def once(conn, sql, label):
        calls["n"] += 1
        if calls["n"] == 2:
            raise psycopg2.Error("relation already exists")

    monkeypatch.setattr(app_mig, "execute_migration", once)
    conn = FakeConn()

    app_mig.trial(conn, "SELECT 1;", require_idempotent=False)      # must not raise

    assert "not idempotent" in capsys.readouterr().out


def test_a_non_idempotent_file_is_fatal_when_required(monkeypatch):
    calls = {"n": 0}

    def once(conn, sql, label):
        calls["n"] += 1
        if calls["n"] == 2:
            raise psycopg2.Error("relation already exists")

    monkeypatch.setattr(app_mig, "execute_migration", once)

    with pytest.raises(SystemExit) as exc:
        app_mig.trial(FakeConn(), "SELECT 1;", require_idempotent=True)

    assert exc.value.code == 3


# ── diffs and ledger ─────────────────────────────────────────────────────────

def test_count_changes_counts_objects_not_detail_lines():
    delta = ["  + column public.a.b", "  ~ column public.a.c",
             "      was: text", "      now: uuid", "  - index idx_a"]

    assert app_mig.count_changes(delta) == 3


def test_schema_diff_classifies_added_changed_and_removed():
    before = {"column public.a.b": "text", "index idx_a": "btree"}
    after = {"column public.a.b": "uuid", "column public.a.c": "text"}

    text = "\n".join(app_mig.schema_diff(before, after))

    assert "+ column public.a.c" in text
    assert "~ column public.a.b" in text
    assert "- index idx_a" in text


def test_status_distinguishes_applied_from_edited(tmp_path, capsys):
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    applied = migrations / "024_x.sql"
    applied.write_text("SELECT 1;", encoding="utf-8")
    edited = migrations / "025_y.sql"
    edited.write_text("SELECT 2;", encoding="utf-8")

    (ledger / "024_x.json").write_text(json.dumps({
        "sha256": hashlib.sha256(applied.read_bytes()).hexdigest(),
        "applied_at": "2026-09-13T05:37:43+00:00"}), encoding="utf-8")
    (ledger / "025_y.json").write_text(json.dumps({
        "sha256": "0" * 64, "applied_at": "2026-09-13T05:37:43+00:00"}), encoding="utf-8")

    app_mig.status(ledger, migrations)

    out = capsys.readouterr().out
    assert "applied 2026-09-13" in out
    assert "APPLIED, THEN EDITED" in out


def test_the_ledger_entry_names_the_file_the_hash_and_who(tmp_path):
    path = tmp_path / "026_z.sql"
    path.write_text("SELECT 1;", encoding="utf-8")

    entry = app_mig.record(tmp_path / "ledger", path, "abc123", ["  + x"], "/tmp/snap.tar.gz")
    payload = json.loads(entry.read_text(encoding="utf-8"))

    assert payload["file"] == "026_z.sql"
    assert payload["sha256"] == "abc123"
    assert payload["recovery_point"] == "/tmp/snap.tar.gz"
    assert payload["by"] and payload["by"] != "?@"


# ── there is no way to skip the trial ────────────────────────────────────────

def _main_calls(name):
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            calls = [n for n in ast.walk(node)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name)
                     and n.func.id == name]
            return sorted(c.lineno for c in calls)
    raise AssertionError("main() not found")


def test_the_dry_run_runs_before_anything_is_committed():
    trials = _main_calls("dry_run")
    commits = _main_calls("commit_run")

    assert trials and commits, "main must call both"
    assert max(trials) < min(commits), (
        "commit_run must never be reachable before the trial has run")


def test_the_migration_sql_is_executed_in_exactly_two_places():
    """One trialled path (``execute_migration``, called by ``trial``) and one
    committing path (``commit_run``). A third call site would be a route that
    applies SQL without ever trialling it, so the count is a tripwire."""
    assert SOURCE.count("cur.execute(sql)") == 2, (
        "a new call site that executes the migration file must either trial it "
        "first or be justified here")
    assert "def trial(" in SOURCE
    assert "ROLLBACK TO SAVEPOINT" in SOURCE, "the re-run must not poison the transaction"


def test_a_recovery_point_is_taken_before_the_commit_path_applies(monkeypatch, capsys):
    taken = {"called": False}

    def snapshot(url, repo, out_dir, keep, label):
        taken["called"] = True
        return Path("/tmp/snap.tar.gz")

    monkeypatch.setattr(app_mig, "take_recovery_point", snapshot)
    monkeypatch.setattr(app_mig, "connect", lambda url: FakeConn())
    monkeypatch.setattr(app_mig, "schema_snapshot", lambda cur: {})

    app_mig.commit_run("postgresql://x", Path("026_z.sql"), "SELECT 1;", Path("."),
                       Path("/tmp"), 5, "026_z", snapshot=True)

    assert taken["called"], "the recovery point must be taken before applying"


# ── verifying against the schema, which settles what the ledger cannot ───────

def _write_migrations(tmp_path, sources):
    directory = tmp_path / "migrations"
    directory.mkdir(exist_ok=True)
    for name, sql in sources.items():
        (directory / name).write_text(sql, encoding="utf-8")
    return directory


def _verify(monkeypatch, tmp_path, sources, live, recorded=None):
    """Run verify against a scripted catalogue. Returns (exit code, captured)."""
    migrations = _write_migrations(tmp_path, sources)
    ledger = tmp_path / "ledger"
    ledger.mkdir(exist_ok=True)
    for name, payload in (recorded or {}).items():
        (ledger / f"{Path(name).stem}.json").write_text(
            json.dumps(payload), encoding="utf-8")

    captured = {}

    def snapshot(cur, extra_schemas=()):
        captured["extra_schemas"] = extra_schemas
        return live

    monkeypatch.setattr(app_mig, "schema_snapshot", snapshot)
    return app_mig.verify(ledger, migrations, object()), captured


def test_several_add_column_clauses_name_the_table_once():
    """`ALTER TABLE t ADD COLUMN a, ADD COLUMN b` names `t` only at the front, so
    a pattern that expects `ALTER TABLE` before every clause loses half the row."""
    keys = [key for _, key, _ in app_mig.declared_objects(
        "ALTER TABLE classes ADD COLUMN a text, ADD COLUMN b int;")]

    assert keys == ["column classes.a", "column classes.b"]


def test_every_ddl_kind_is_keyed_the_way_the_snapshot_names_it():
    """Membership in the snapshot is the whole test, so a key that does not match
    the snapshot's spelling reports a present object as missing."""
    keys = {key for _, key, _ in app_mig.declared_objects("""
        CREATE TABLE IF NOT EXISTS pengumuman (id uuid);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_a ON pengumuman (id);
        ALTER TABLE pengumuman ADD CONSTRAINT pengumuman_pkey PRIMARY KEY (id);
        CREATE POLICY "read_own" ON public.pengumuman FOR SELECT USING (true);
        CREATE OR REPLACE FUNCTION public._is_role(role text) RETURNS boolean
            AS $$ SELECT true $$ LANGUAGE sql;
        CREATE TRIGGER trg_touch BEFORE UPDATE ON pengumuman
            FOR EACH ROW EXECUTE FUNCTION public.touch();
    """)}

    assert keys == {"table pengumuman", "index idx_a", "constraint pengumuman_pkey",
                    "policy pengumuman.read_own", "function _is_role",
                    "trigger trg_touch"}


def test_a_schema_qualified_policy_is_keyed_by_its_table_not_its_schema():
    """`ON storage.objects` is the case that matters: reading the qualifier as the
    table is how a present storage policy gets reported as absent."""
    keys = [key for _, key, _ in app_mig.declared_objects(
        "CREATE POLICY exam_pdfs_select ON storage.objects FOR SELECT USING (true);")]

    assert keys == ["policy objects.exam_pdfs_select"]


def test_ddl_inside_a_dollar_body_is_counted_rather_than_guessed():
    """A policy built by dynamic SQL cannot be read out of the file at all. It is
    reported as unreadable instead of silently missing, because an object nobody
    checked must not look like an object that is there."""
    text, hidden = app_mig.mask_do_bodies(app_mig.strip_sql_comments("""
        DO $$ BEGIN
            EXECUTE 'CREATE POLICY built_at_runtime ON pengumuman ...';
        EXCEPTION WHEN undefined_table THEN NULL; END $$;
    """))

    assert hidden == 1
    assert app_mig.declared_objects(text) == []


def test_an_object_its_own_file_creates_and_drops_is_not_a_gap(monkeypatch, tmp_path,
                                                               capsys):
    """024's `school_id_new` is absent *because* the migration finished. Reporting
    it as missing would make the one file that worked look like the broken one."""
    code, _ = _verify(monkeypatch, tmp_path, {"024_x.sql": """
        ALTER TABLE pengumuman ADD COLUMN school_id_new uuid;
        UPDATE pengumuman SET school_id_new = NULL;
        ALTER TABLE pengumuman DROP COLUMN school_id_new;
    """}, live={"table pengumuman"})

    assert code == 0
    assert "MISSING" not in capsys.readouterr().out


def test_an_object_another_file_replaces_is_reported_as_replaced(monkeypatch, tmp_path,
                                                                capsys):
    """With the ledger empty, "absent" has to be told apart from "replaced by a
    later file" -- otherwise every superseded policy reads as a migration that
    never ran."""
    code, _ = _verify(monkeypatch, tmp_path, {
        "001_old.sql": "CREATE POLICY old_name ON pengumuman FOR SELECT USING (true);",
        "007_new.sql": (
            "DROP POLICY old_name ON pengumuman;\n"
            "CREATE POLICY new_name ON pengumuman FOR SELECT USING (true);"),
    }, live={"policy pengumuman.new_name"})

    out = capsys.readouterr().out
    assert code == 0, out
    assert "MISSING" not in out


def test_a_genuinely_missing_object_is_a_gap_with_its_own_exit_code(monkeypatch,
                                                                    tmp_path, capsys):
    code, _ = _verify(monkeypatch, tmp_path, {
        "014_x.sql": (
            "CREATE POLICY ai_logs_select_own ON ai_grading_logs FOR SELECT USING (true);")},
        live={"table ai_grading_logs"})

    out = capsys.readouterr().out
    assert code == 6, out
    assert "MISSING  policy ai_grading_logs.ai_logs_select_own" in out


def test_a_policy_cannot_be_created_when_its_table_does_not_exist(monkeypatch, tmp_path,
                                                                 capsys):
    """This is the 20260608 failure: it declares policies on `activation_codes`,
    which no migration ever creates, so the file could never have completed. The
    report names the cause instead of only the symptom."""
    code, _ = _verify(monkeypatch, tmp_path, {"20260608_x.sql": (
        "CREATE POLICY codes_select_admin ON activation_codes FOR SELECT USING (true);")},
        live={})

    out = capsys.readouterr().out
    assert code == 6
    assert "its table `activation_codes` does not exist" in out


def test_a_file_with_nothing_to_check_is_not_counted_as_missing(monkeypatch, tmp_path,
                                                               capsys):
    """`001_initial_schema.sql` is a placeholder ('copy schema.sql here') and
    `020_backfill_conversations.sql` only moves data. Neither can be settled from
    the catalogue, and calling either missing would be a false alarm."""
    code, _ = _verify(monkeypatch, tmp_path, {
        "001_initial_schema.sql": "-- Migration 001: Initial schema\n-- Copy content from supabase/schema.sql\n",
        "020_backfill.sql": "UPDATE conversations SET x = 1;\n",
    }, live={})

    out = capsys.readouterr().out
    assert code == 0, out
    assert "2 with no objects to check" in out
    assert "MISSING" not in out


def test_verify_asks_for_the_storage_schema(monkeypatch, tmp_path):
    """The app owns policies on `storage.objects`, and a catalogue without that
    schema reported `003_add_storage_rls.sql` as absent when it is present. This
    pins the reason `extra_schemas` exists at all."""
    _, captured = _verify(monkeypatch, tmp_path, {"003_x.sql": "SELECT 1;"}, live={})

    assert captured["extra_schemas"] == ("storage",)
    assert "ANY(%s)" in SOURCE, (
        "the catalogue must be asked for a schema list, or storage is invisible")


def test_the_report_puts_the_schema_verdict_beside_the_ledger_note(monkeypatch,
                                                                  tmp_path, capsys):
    """The point of the mode: 'no record' becomes an answer. A file the ledger has
    never heard of still reads as IN when its objects are in the schema."""
    sql = "CREATE TABLE widget (id uuid);\n"
    code, _ = _verify(monkeypatch, tmp_path, {"026_widget.sql": sql},
                      live={"table widget"})

    out = capsys.readouterr().out
    assert code == 0
    assert "026_widget.sql" in out and "IN" in out and "no record" in out


def test_an_edited_file_is_still_called_out(monkeypatch, tmp_path, capsys):
    """`applied-then-edited` is a ledger fact and the schema cannot see it, so the
    report must keep showing both rather than letting the schema verdict replace it."""
    sql = "CREATE TABLE widget (id uuid);\n"
    code, _ = _verify(monkeypatch, tmp_path, {"026_widget.sql": sql},
                      live={"table widget"},
                      recorded={"026_widget.sql": {"sha256": "0" * 64,
                                                  "applied_at": "2026-09-13T05:37:43+00:00"}})

    out = capsys.readouterr().out
    assert code == 0
    assert "APPLIED, THEN EDITED" in out


def test_the_verify_session_cannot_write(monkeypatch):
    """Producing a report must not be able to change the thing it reports on."""
    calls = {}

    class _Conn:
        def set_session(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(app_mig.psycopg2, "connect", lambda url: _Conn())

    app_mig.connect_readonly("postgresql://x")

    assert calls == {"readonly": True, "autocommit": True}


def test_verify_never_records_anything_in_the_ledger(monkeypatch, tmp_path):
    """Only `--commit` writes a ledger entry; a report that did would turn an
    observation into a claim."""
    _, _ = _verify(monkeypatch, tmp_path, {"026_widget.sql": "CREATE TABLE w (id uuid);"},
                   live={"table w"})

    assert list((tmp_path / "ledger").glob("*.json")) == []


def test_the_cli_offers_verify_without_needing_a_file():
    assert 'add_argument("--verify"' in SOURCE
    assert "if args.verify:" in SOURCE
    assert "connect_readonly" in SOURCE, "verify must not open a writable session"


# ── identifiers the SQL files spell in quotes ────────────────────────────────

def _declared_from(path: Path) -> set[str]:
    """The keys `--verify` would compute for a real file on disk."""
    masked, _hidden = app_mig.mask_do_bodies(
        app_mig.strip_sql_comments(path.read_text(encoding="utf-8-sig")))
    return {key for _kind, key, _index in app_mig.declared_objects(masked)}


def test_a_quoted_table_name_is_read_whole():
    """`"school-payment-demo"` is the case that mattered: the bare pattern stopped
    at the hyphen, so the object was read as `school` and a table that is present in
    the catalogue was reported missing — which is what made 027 read PARTIAL."""
    keys = [key for _, key, _ in app_mig.declared_objects(
        'CREATE TABLE IF NOT EXISTS "school-payment-demo" (id BIGINT);')]

    assert keys == ["table school-payment-demo"]


def test_a_quoted_policy_name_is_read_whole_and_its_drop_matches():
    declared = [key for _, key, _ in app_mig.declared_objects(
        'CREATE POLICY "read own" ON schools FOR SELECT USING (true);')]
    dropped = app_mig.dropped_names('DROP POLICY IF EXISTS "read own" ON schools;')

    assert declared == ["policy schools.read own"]
    assert list(dropped) == ["read own"], (
        "a drop is matched against `bare_name`, so a quoted drop has to dequote to "
        "the same spelling the declaration produced")


def test_an_unquoted_name_is_folded_the_way_postgres_folds_it():
    """Postgres lower-cases an unquoted identifier, and the snapshot's keys come
    from the catalogue — so `CREATE TABLE Foo` is `foo` and nothing else."""
    keys = [key for _, key, _ in app_mig.declared_objects("CREATE TABLE Foo (id uuid);")]

    assert keys == ["table foo"]


def test_the_real_027_reads_its_quoted_table_off_the_file():
    """Tied to the file the fix was written for, so it cannot decay into a unit test
    of a regex that no migration in this repository exercises."""
    path = ROOT / "supabase" / "migrations" / "027_schema_contract_drift.sql"

    assert "table school-payment-demo" in _declared_from(path)


# ── the objects a later generation replaced under a different name ───────────

def test_every_superseded_entry_names_a_real_file_and_a_reason():
    for file_name, entries in app_mig.SUPERSEDED.items():
        path = ROOT / "supabase" / "migrations" / file_name
        assert path.is_file(), f"{file_name} is not a migration file"
        for key, reason in entries.items():
            assert reason.strip(), f"{file_name}: {key} carries no reason"


def test_every_superseded_entry_names_something_its_file_really_declares():
    """The table can only excuse an object the file declares. An entry that stops
    matching is a reason nobody reads — and the next absent object would then be
    excused by nothing at all, which is the state this table exists to prevent."""
    for file_name, entries in app_mig.SUPERSEDED.items():
        declared = _declared_from(ROOT / "supabase" / "migrations" / file_name)
        for key in entries:
            assert key in declared, f"{file_name} no longer declares {key}"


def test_a_named_superseded_object_is_reported_with_its_reason_not_as_a_gap(
        monkeypatch, tmp_path, capsys):
    code, _ = _verify(monkeypatch, tmp_path, {
        "001_enable_rls_and_policies.sql":
            "CREATE POLICY schools_read_own ON schools FOR SELECT USING (true);",
    }, live={"table schools"})

    out = capsys.readouterr().out
    assert code == 0, out
    assert "MISSING" not in out
    assert "SUPERSEDED  policy schools.schools_read_own" in out
    assert "_COMPLETE_SETUP.sql" in out


def test_the_named_exception_does_not_leak_to_another_file(monkeypatch, tmp_path,
                                                           capsys):
    """The entry is keyed by file as well as by object, so the same statement in a
    different migration is still judged as whatever that file makes of it."""
    code, _ = _verify(monkeypatch, tmp_path, {
        "099_something_else.sql":
            "CREATE POLICY schools_read_own ON schools FOR SELECT USING (true);",
    }, live={"table schools"})

    out = capsys.readouterr().out
    assert code == 6, out
    assert "MISSING  policy schools.schools_read_own" in out


# ── adopting one that is already applied ─────────────────────────────────────
#
# `--commit` writes the record because it watched the migration run. A migration
# applied by hand, by an older copy of this tool, or from a workstation whose
# ledger never reached the box has no such witness: the ledger says "no record"
# while the catalogue plainly carries the objects. `--adopt` ends that
# disagreement, and three properties are what make it honest rather than a way to
# write "applied" over anything:
#
# * the record is written from the **live schema**, never from the operator's
#   memory — a record the database contradicts is the one lie a ledger must not
#   tell, and it is the state somebody is trying to leave;
# * the recovery point is **moved somewhere durable and pinned**, because a
#   record naming an archive in a temp directory is a promise nobody can keep;
# * the entry **says `adopted`**, because who wrote the line and who watched the
#   migration run are different facts.

def _adopt_setup(monkeypatch, tmp_path, *, live, sql="CREATE TABLE widget (id uuid);\n",
                 name="062_widget.sql",
                 archive_name="scangrade-db-20261009T094552Z-062_widget.tar.gz"):
    repo = tmp_path / "repo"
    migrations = repo / "supabase" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / name).write_text(sql, encoding="utf-8")
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    archive = incoming / archive_name
    archive.write_bytes(b"archive-bytes")
    durable = tmp_path / "durable"
    ledger = tmp_path / "ledger"

    monkeypatch.setattr(app_mig, "load_migration_url", lambda repo: "postgresql://x")
    monkeypatch.setattr(app_mig, "check_target", lambda url, repo: REF)
    monkeypatch.setattr(app_mig, "connect_readonly", lambda url: FakeConn())
    monkeypatch.setattr(app_mig, "schema_snapshot",
                        lambda cur, extra_schemas=(): live)
    monkeypatch.setattr(sys, "argv", [
        "apply_migration.py", str(migrations / name), "--adopt", str(archive),
        "--repo", str(repo), "--out", str(durable), "--ledger", str(ledger)])
    return archive, durable, ledger


def _entry(ledger, name="062_widget"):
    return json.loads((ledger / f"{name}.json").read_text(encoding="utf-8"))


def test_adopt_records_it_from_the_live_schema_and_moves_the_recovery_point(
        monkeypatch, tmp_path, capsys):
    archive, durable, ledger = _adopt_setup(monkeypatch, tmp_path,
                                            live={"table widget"})

    assert app_mig.main() == 0
    out = capsys.readouterr().out

    moved = durable / archive.name
    assert moved.is_file() and moved.read_bytes() == b"archive-bytes"
    assert archive.is_file(), "the default must keep the original, not move it away"
    assert app_mig.db_snapshot.read_pins(durable) == {archive.name}, (
        "the recovery point was moved but not pinned, so `--keep 5` will delete it")
    assert "pinned so rotation cannot delete it" in out

    payload = _entry(ledger)
    assert payload["adopted"] is True, "an adopted record must say so"
    assert payload["adopted_from"] == str(archive)
    assert payload["recovery_point"] == str(moved), (
        "the record names where the archive is, and it is not there")
    assert payload["applied_at"].startswith("2026-10-09T09:45:52"), (
        "the archive's own timestamp is the closest thing to a witnessed when")
    assert "+ table widget" in payload["schema_changes"]
    assert "adopted" in out and "not re-run by this mode" in out


def test_adopt_refuses_when_the_live_schema_contradicts_the_file(
        monkeypatch, tmp_path, capsys):
    """The refusal that makes the mode worth having: it cannot be used to write
    "applied" over a migration that never ran."""
    archive, durable, ledger = _adopt_setup(monkeypatch, tmp_path, live={})

    assert app_mig.main() == 6
    out = capsys.readouterr().out

    assert "REFUSED" in out and "MISSING  table widget" in out
    assert not (ledger / "062_widget.json").exists(), "it recorded anyway"
    assert not durable.exists(), "it moved the recovery point anyway"


def test_adopt_only_reads_the_database(monkeypatch, tmp_path):
    """The mode's whole claim is that it applies nothing, so it asks the catalogue
    through the read-only session `--verify` uses — and never `connect()`."""
    seen = []

    def readonly(url):
        seen.append(url)
        return FakeConn()

    _adopt_setup(monkeypatch, tmp_path, live={"table widget"})
    monkeypatch.setattr(app_mig, "connect_readonly", readonly)
    monkeypatch.setattr(app_mig, "connect",
                        lambda url: pytest.fail("adopt opened a writable session"))

    assert app_mig.main() == 0

    assert seen == ["postgresql://x"]


def test_the_original_is_removed_only_when_prune_source_asks(monkeypatch, tmp_path):
    archive, durable, _ledger = _adopt_setup(monkeypatch, tmp_path, live={"table widget"})
    sys.argv.append("--prune-source")

    assert app_mig.main() == 0

    assert not archive.exists(), "--prune-source left the original behind"
    assert (durable / archive.name).is_file()


def test_a_copy_that_does_not_match_is_refused_and_the_source_kept(
        monkeypatch, tmp_path, capsys):
    """The state this mode starts from is "the only copy of the archive is
    somewhere odd", so a copy is verified before anything is recorded — and the
    source is never removed on the strength of a copy that is short."""
    archive, durable, ledger = _adopt_setup(monkeypatch, tmp_path,
                                            live={"table widget"})
    sys.argv.append("--prune-source")

    def truncate(src, dst, *a, **k):
        Path(dst).write_bytes(b"x")

    monkeypatch.setattr(app_mig.shutil, "copy2", truncate)

    assert app_mig.main() == 1
    out = capsys.readouterr().out

    assert "FAILED: could not put the recovery point" in out
    assert "copy is 1 bytes" in out
    assert archive.is_file(), "a failed copy removed the only copy of the archive"
    assert not (durable / archive.name).exists(), "a half copy was left in place"
    assert not (ledger / "062_widget.json").exists(), (
        "a record naming an archive that is not there is worse than no record")


def test_the_operators_applied_at_wins_over_the_filename(monkeypatch, tmp_path):
    """Not every archive carries a stamp in its name, and the operator may know
    better than the filename: an explicit date is taken as given."""
    _archive, _durable, ledger = _adopt_setup(monkeypatch, tmp_path,
                                              live={"table widget"},
                                              archive_name="before-seb.tar.gz")
    sys.argv += ["--applied-at", "2026-10-09T12:00:00+00:00"]

    assert app_mig.main() == 0

    assert _entry(ledger)["applied_at"] == "2026-10-09T12:00:00+00:00"


def test_an_archive_with_no_stamp_is_recorded_without_a_guess(monkeypatch, tmp_path):
    _archive, _durable, ledger = _adopt_setup(monkeypatch, tmp_path,
                                              live={"table widget"},
                                              archive_name="before-seb.tar.gz")

    assert app_mig.main() == 0

    payload = _entry(ledger)
    assert payload["applied_at"] and not payload["applied_at"].startswith("before"), (
        "an unreadable stamp must not be written as the date")


def test_adopt_needs_the_file_it_records(monkeypatch, tmp_path):
    """Recording an archive without saying which migration it was taken for would
    put an entry in the ledger that names nothing."""
    _adopt_setup(monkeypatch, tmp_path, live={"table widget"})
    sys.argv = ["apply_migration.py", "--adopt", str(tmp_path / "incoming" /
                "scangrade-db-20261009T094552Z-062_widget.tar.gz")]

    with pytest.raises(SystemExit) as refused:
        app_mig.main()

    assert refused.value.code == 2


def test_the_cli_offers_adopt_with_the_file_it_records():
    assert 'add_argument("--adopt"' in SOURCE
    assert 'add_argument("--applied-at"' in SOURCE
    assert 'add_argument("--prune-source"' in SOURCE
    assert "if args.adopt:" in SOURCE
    # One catalogue read, `storage` included: the app owns policies there, and a
    # check that cannot see them calls a present file absent. **Three** readers now
    # — verify, adopt and reconcile — because a reconcile that judged a different
    # catalogue from the report a person reads is how a record and a report come to
    # disagree about one file.
    assert SOURCE.count('schema_snapshot(cur, extra_schemas=("storage",))') == 3, (
        "verify, adopt and reconcile must judge the same catalogue, storage "
        "policies included")


def test_the_status_report_says_where_the_recovery_point_is(tmp_path, capsys):
    """A record reading "applied" looks complete on its own, which is how a box
    ends up with a migration it can no longer undo. Where the archive is, and
    whether rotation can delete it, are printed under the file."""
    migrations = _write_migrations(tmp_path, {"026_widget.sql": "SELECT 1;\n"})
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    archive = tmp_path / "durable" / "scangrade-db-20260901T000000Z-026_widget.tar.gz"
    archive.parent.mkdir()
    archive.write_bytes(b"x")
    digest = hashlib.sha256((migrations / "026_widget.sql").read_bytes()).hexdigest()
    (ledger / "026_widget.json").write_text(json.dumps({
        "sha256": digest, "applied_at": "2026-09-01T00:00:00+00:00",
        "adopted": True, "recovery_point": str(archive)}), encoding="utf-8")

    assert app_mig.status(ledger, migrations) == 0
    out = capsys.readouterr().out

    assert "adopted 2026-09-01" in out, "the record's own note lost `adopted`"
    assert f"recovery point: {archive} (NOT PINNED" in out, (
        "an archive rotation will delete has to be named as such")

    app_mig.db_snapshot.pin_archive(archive)
    assert app_mig.status(ledger, migrations) == 0
    assert "(pinned)" in capsys.readouterr().out


def test_a_recovery_point_that_is_gone_reads_as_missing(tmp_path, capsys):
    """`--restore` cannot restore an archive that is not there, and a record that
    does not say so is a recovery plan nobody has tested."""
    migrations = _write_migrations(tmp_path, {"026_widget.sql": "SELECT 1;\n"})
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    digest = hashlib.sha256((migrations / "026_widget.sql").read_bytes()).hexdigest()
    (ledger / "026_widget.json").write_text(json.dumps({
        "sha256": digest, "applied_at": "2026-09-01T00:00:00+00:00",
        "recovery_point": str(tmp_path / "gone.tar.gz")}), encoding="utf-8")

    assert app_mig.status(ledger, migrations) == 0
    out = capsys.readouterr().out

    assert "recovery point: MISSING" in out


# ── reconciling the ledger with the schema ───────────────────────────────────
#
# The two-ledger problem, and why it has to be settled from inside the box: a
# migration applied from a workstation (which is how this repository's migrations
# are applied — nothing can log into the VPS) leaves the box's `--status` saying
# "no record", which means *unknown*, while the catalogue plainly carries the
# objects. `--verify` answers that question from the schema; `--reconcile` writes
# the answer down, on the box's own tick, because a box with no shell never gets
# an operator to run `--adopt` by hand.
#
# Three properties make it honest rather than a way to write "applied" over
# anything:
#
# * a record is written only for a file the **catalogue confirms** — one whose
#   objects are missing is reported and left alone, which is the same finding
#   `--verify` exits 6 with;
# * the record says `adopted`, because this box did not watch the migration run;
# * a recovery point is named only when an archive can be **proved to predate the
#   migration** (an archive older than the commit that first added the file cannot
#   contain it), so restoring that archive really does undo it. Every other case
#   records the migration with no archive named and says why — a snapshot taken
#   afterwards is not a recovery point for it.

def _reconcile_setup(monkeypatch, tmp_path, *, live, files, archives=(),
                     archives_in_place=False):
    repo = tmp_path / "repo"
    migrations = repo / "supabase" / "migrations"
    migrations.mkdir(parents=True)
    for name, sql in files.items():
        (migrations / name).write_text(sql, encoding="utf-8")
    out = tmp_path / "backups"
    landing = out if archives_in_place else tmp_path / "incoming"
    landing.mkdir(parents=True)
    for name in archives:
        (landing / name).write_bytes(b"archive-bytes")
    ledger = tmp_path / "ledger"

    monkeypatch.setattr(app_mig, "load_migration_url", lambda repo: "postgresql://x")
    monkeypatch.setattr(app_mig, "check_target", lambda url, repo: REF)
    monkeypatch.setattr(app_mig, "connect_readonly", lambda url: FakeConn())
    monkeypatch.setattr(app_mig, "schema_snapshot",
                        lambda cur, extra_schemas=(): live)
    monkeypatch.setattr(sys, "argv", [
        "apply_migration.py", "--reconcile", "--repo", str(repo),
        "--out", str(out), "--ledger", str(ledger)])
    return migrations, out, ledger


def test_reconcile_records_what_the_schema_confirms(monkeypatch, tmp_path, capsys):
    """The whole point: `no record` becomes a record, and `--status` then answers
    the question instead of deferring it to a database."""
    migrations, _out, ledger = _reconcile_setup(
        monkeypatch, tmp_path, live={"table widget"},
        files={"064_widget.sql": "CREATE TABLE widget (id uuid);\n"})

    assert app_mig.main() == 0
    printed = capsys.readouterr().out

    payload = _entry(ledger, "064_widget")
    assert payload["adopted"] is True, (
        "a record written by this box for something it never watched says `adopted`")
    assert payload["recovery_point"] is None, (
        "it named a recovery point while holding no archive that can undo this")
    assert "+ table widget" in payload["schema_changes"], (
        "the record was not written from the catalogue it checked")
    assert "reconciled: 1 record(s) written" in printed
    assert "no archive on this box can undo this" in printed

    assert app_mig.status(ledger, migrations) == 0
    shown = capsys.readouterr().out
    assert "0 of 1 have no record." in shown, "the file is still unrecorded"
    assert re.search(r"adopted \d{4}-\d{2}-\d{2}", shown), (
        "the ledger still reads 'no record' for a migration it has just recorded")
    assert "recovery point: none recorded" in shown, (
        "a record with no archive must say so rather than look complete")


def test_the_recovery_point_is_the_archive_taken_for_that_migration(monkeypatch,
                                                                    tmp_path):
    """A `recovery_point` claims one thing — restoring this archive undoes that
    migration — and the only proof of it is the archive's own name: the run that
    applies a file takes a snapshot immediately before it and labels it with the
    file's stem. So an archive taken for another file, or taken for a release, is
    never named, however old or new it is."""
    _migrations, out, ledger = _reconcile_setup(
        monkeypatch, tmp_path, live={"table widget"},
        files={"064_widget.sql": "CREATE TABLE widget (id uuid);\n"},
        archives=["scangrade-db-20260928T000000Z-039_other_migration.tar.gz",
                  "scangrade-db-20261009T235959Z-064_widget.tar.gz",
                  "scangrade-db-20261010T040000Z-064_widget.tar.gz",
                  "scangrade-db-20261010T050000Z-deadbeef.tar.gz",
                  "copied-by-hand.tar.gz"],
        archives_in_place=True)

    assert app_mig.main() == 0

    # The newest one taken *for this file*: a migration applied twice has two, and
    # the later one is the state the second application started from.
    chosen = "scangrade-db-20261010T040000Z-064_widget.tar.gz"
    payload = _entry(ledger, "064_widget")
    assert payload["recovery_point"].endswith(chosen), (
        "the archive named was not the one taken for this migration — 039's archive, "
        "a deploy's commit-labelled snapshot and a hand-copied file cannot undo it")
    assert payload["recovery_point_basis"].startswith("its own name"), (
        "the record must carry why this archive was chosen")
    assert app_mig.db_snapshot.read_pins(out) == {chosen}, (
        "the one archive that can undo the migration is rotation fodder again")


def test_an_archive_taken_for_another_file_is_not_a_recovery_point(monkeypatch,
                                                                   tmp_path, capsys):
    """The refusal that makes the mode worth having, and the case a live run caught:
    a box holding 062's and 063's snapshots but not 064's had one named for 064 under
    a date rule, and restoring it would have left the migration exactly where it was."""
    _migrations, out, ledger = _reconcile_setup(
        monkeypatch, tmp_path, live={"table widget"},
        files={"064_widget.sql": "CREATE TABLE widget (id uuid);\n"},
        archives=["scangrade-db-20261010T034327Z-062_widget.tar.gz",
                  "scangrade-db-20261010T054218Z-063_widget.tar.gz"],
        archives_in_place=True)

    assert app_mig.main() == 0
    printed = capsys.readouterr().out

    payload = _entry(ledger, "064_widget")
    assert payload["adopted"] is True, "the migration itself must still be recorded"
    assert payload["recovery_point"] is None
    assert "-064_widget.tar.gz" in printed, (
        "the output does not name the archive that would settle it")
    assert "--adopt <archive>" in printed, (
        "the operator is not told how to name the archive that lives elsewhere")
    assert app_mig.db_snapshot.read_pins(out) == set(), (
        "an archive taken for another migration was pinned as this one's recovery "
        "point")


def test_a_file_the_schema_contradicts_is_left_unrecorded(monkeypatch, tmp_path,
                                                          capsys):
    """A record saying "applied" over a schema that contradicts it is the one lie a
    ledger must never tell — and this mode writes records nobody has read before
    writing them.

    The run still **succeeds**: ruling on whether the schema is behind the release's
    code is `--verify`'s job (it exits 6, and the deploy's gate quarantines on that),
    and a second, differently-worded verdict from here would either cry wolf on every
    release or wave one through."""
    _migrations, out, ledger = _reconcile_setup(
        monkeypatch, tmp_path, live={},
        files={"064_widget.sql": "CREATE TABLE widget (id uuid);\n"})

    assert app_mig.main() == 0, (
        "the reconcile reported the schema being behind the code as its own failure")
    printed = capsys.readouterr().out

    assert "not recorded: 064_widget.sql" in printed
    assert "MISSING  table widget" in printed
    assert "0 record(s) written" in printed
    assert not (ledger / "064_widget.json").exists(), (
        "it recorded a migration the database says is not there")
    assert not out.exists() or not list(out.glob("*.tar.gz"))


def test_a_record_that_already_exists_is_never_overwritten(monkeypatch, tmp_path,
                                                           capsys):
    """`APPLIED, THEN EDITED` is a fact somebody established about a file that has
    changed since. Reconciling is for what is *unrecorded*, so it leaves that alone
    — and leaves it byte for byte, rather than re-serialising it."""
    _migrations, _out, ledger = _reconcile_setup(
        monkeypatch, tmp_path, live={"table widget"},
        files={"064_widget.sql": "CREATE TABLE widget (id uuid);\n"})
    ledger.mkdir()
    entry = ledger / "064_widget.json"
    entry.write_text(json.dumps({"file": "064_widget.sql", "sha256": "0" * 64,
                                 "applied_at": "2026-01-01T00:00:00+00:00",
                                 "recovery_point": None}) + "\n", encoding="utf-8")
    before = entry.read_bytes()

    assert app_mig.main() == 0
    printed = capsys.readouterr().out

    assert "0 with no record" in printed
    assert "Nothing to reconcile" in printed
    assert entry.read_bytes() == before, "the reconcile overwrote somebody's record"


def test_a_file_the_catalogue_cannot_settle_is_not_recorded_either(
        monkeypatch, tmp_path, capsys):
    """A data-only migration declares no object, so the catalogue cannot confirm it
    either way — and a record reading `adopted` would be a claim that it can."""
    _migrations, _out, ledger = _reconcile_setup(
        monkeypatch, tmp_path, live={"table widget"},
        files={"099_backfill.sql": "UPDATE widget SET id = id;\n"})

    assert app_mig.main() == 0
    printed = capsys.readouterr().out

    assert not (ledger / "099_backfill.json").exists()
    assert "unknown" in printed and "099_backfill.sql" in printed
    assert "0 record(s) written" in printed


def test_reconcile_only_reads_the_database(monkeypatch, tmp_path):
    """It writes records, not DDL: the catalogue is read through the read-only
    session `--verify` uses, and never through `connect()`."""
    seen = []

    def readonly(url):
        seen.append(url)
        return FakeConn()

    _reconcile_setup(monkeypatch, tmp_path, live={"table widget"},
                     files={"064_widget.sql": "CREATE TABLE widget (id uuid);\n"})
    monkeypatch.setattr(app_mig, "connect_readonly", readonly)
    monkeypatch.setattr(app_mig, "connect",
                        lambda url: pytest.fail("reconcile opened a writable session"))

    assert app_mig.main() == 0

    assert seen == ["postgresql://x"]


# ── which archive is the migration's own ────────────────────────────────────

def test_the_archive_is_chosen_by_the_label_the_applying_run_wrote(tmp_path):
    """The name is the proof, so only the file's own label counts — a deploy's
    snapshot carries the commit it was taken for, and a hand-copied archive carries
    nothing that can be checked."""
    out = tmp_path / "backups"
    out.mkdir()
    for name in ("scangrade-db-20260928T000000Z-064_widget.tar.gz",
                 "scangrade-db-20261010T040000Z-064_widget.tar.gz",
                 "scangrade-db-20261010T050000Z-064_widget_extra.tar.gz",
                 "scangrade-db-20261011T000000Z-deadbeef.tar.gz",
                 "hand-copied.tar.gz"):
        (out / name).write_bytes(b"x")
    path = tmp_path / "064_widget.sql"
    path.write_text("SELECT 1;\n", encoding="utf-8")

    chosen = app_mig.own_archive(out, path)

    assert chosen.name == "scangrade-db-20261010T040000Z-064_widget.tar.gz", (
        "an archive taken for another file, or a longer label, was taken as this "
        "file's recovery point")
    assert app_mig.own_archive(tmp_path / "absent", path) is None, (
        "a backup directory that is not there must not read as an archive")
    other = tmp_path / "065_other.sql"
    other.write_text("SELECT 1;\n", encoding="utf-8")
    assert app_mig.own_archive(out, other) is None, (
        "a stem that is a prefix of another migration's archive matched it")


def test_the_cli_offers_reconcile_and_status_points_at_it():
    assert 'add_argument("--reconcile"' in SOURCE
    assert "if args.reconcile:" in SOURCE
    assert "`--reconcile` records it from the" in SOURCE, (
        "`--status` still tells the reader to adopt by hand and nothing else — on "
        "the box that has no shell, that advice is a dead end")

