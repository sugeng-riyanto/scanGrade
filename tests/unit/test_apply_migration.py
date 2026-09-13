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
import sys
from pathlib import Path

import psycopg2
import pytest

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
