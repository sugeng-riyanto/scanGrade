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
import io
import json
import subprocess
import sys
import tarfile
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


# ── one file, one identity, on every platform ────────────────────────────────
#
# The digest is the whole of the ledger's answer to "has this file been edited since
# it was applied?", and it was answering *yes* for files nobody had touched: this
# Windows checkout materialises a committed LF file as CRLF, so one unchanged
# migration carried two shas and the box read `APPLIED, THEN EDITED` — measured here
# for `037` and again for `064_seb_door_refusals.sql`, whose record held exactly the
# sha of the same file with LF inserted. Two things hold that now, and both are
# checked below: the ledger folds CRLF before hashing, so the digest cannot depend on
# the platform, and the repository pins the extension to LF, so the file itself
# cannot either.

#: One migration's bytes: as the committing machine stores them, and as a Windows
#: checkout of the same commit holds them.
LF_SQL = b"CREATE TABLE widget (id uuid);\nSELECT 1;\n"
CRLF_SQL = LF_SQL.replace(b"\n", b"\r\n")


def _bytes(path: Path, body: bytes) -> Path:
    """Fixture bytes written literally. `write_text` translates `\n` to `\r\n` on
    Windows, and a fixture whose line endings depend on the machine that writes it
    cannot measure line endings."""
    path.write_bytes(body)
    return path


def _record(path: Path, body: bytes) -> None:
    """The record the Linux box would have written after applying `body`."""
    path.write_text(json.dumps({"sha256": hashlib.sha256(body).hexdigest(),
                                "applied_at": "2026-09-13T05:37:43+00:00"}),
                    encoding="utf-8")


def test_a_crlf_checkout_and_an_lf_box_are_one_identity(tmp_path):
    """One file, two platforms' bytes, one digest — and that digest is the value an
    LF box computes, which is what keeps every sha already on record valid."""
    crlf = _bytes(tmp_path / "crlf.sql", CRLF_SQL)
    lf = _bytes(tmp_path / "lf.sql", LF_SQL)

    assert app_mig.file_digest(crlf) == app_mig.file_digest(lf)
    assert app_mig.file_digest(lf) == hashlib.sha256(LF_SQL).hexdigest()


def test_a_lone_carriage_return_is_a_byte_of_the_file_not_a_line_ending(tmp_path):
    """Only CRLF is folded. A CR that is not part of a line ending is content, and
    folding it would make the identity a claim about a file nobody stored."""
    body = b"SELECT 'a\rb';\n"

    assert app_mig.file_digest(_bytes(tmp_path / "odd.sql", body)) == \
        hashlib.sha256(body).hexdigest()


def test_status_calls_a_crlf_checkout_applied(tmp_path, capsys):
    """The false positive itself, at the surface a human reads."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _bytes(migrations / "024_x.sql", CRLF_SQL)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    _record(ledger / "024_x.json", LF_SQL)

    app_mig.status(ledger, migrations)

    out = capsys.readouterr().out
    assert "applied 2026-09-13" in out
    assert "APPLIED, THEN EDITED" not in out


def test_verify_calls_a_crlf_checkout_applied(monkeypatch, tmp_path, capsys):
    """`--verify` reads the same identity, so the two modes cannot answer differently
    about one file — which is the shape the fix had to cover twice."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _bytes(migrations / "026_widget.sql", CRLF_SQL)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    _record(ledger / "026_widget.json", LF_SQL)
    monkeypatch.setattr(app_mig, "schema_snapshot",
                        lambda cur, extra_schemas=(): {"table widget"})

    code = app_mig.verify(ledger, migrations, object())

    out = capsys.readouterr().out
    assert code == 0
    assert "applied 2026-09-13" in out
    assert "APPLIED, THEN EDITED" not in out


def test_the_record_is_the_digest_of_the_text_that_was_run(tmp_path):
    """The writer's half of one identity, and the only place the two halves of a single
    reading are told apart: `describe_file` hands back the SQL to execute and the sha to
    record, in that order. Swap them and this platform executes a hash and records the
    SQL as the file's identity — while `--status`, `--verify` and the
    one-definition guard all still agree with themselves, because each of them reads a
    *value* and none of them reads the pairing."""
    path = _bytes(tmp_path / "027_widget.sql", CRLF_SQL)

    sql, digest = app_mig.describe_file(path)

    assert sql == CRLF_SQL.decode("utf-8")
    assert digest == hashlib.sha256(LF_SQL).hexdigest()


def test_a_crlf_checkout_records_the_boxs_digest_for_a_file_nobody_touched(tmp_path):
    """The sentence the fix exists for, at the surface that outlives the run: what an
    apply on this platform writes down is the digest the Linux box holds, so the two
    boxes cannot end up disagreeing about a file neither has edited."""
    path = _bytes(tmp_path / "027_widget.sql", CRLF_SQL)

    _, digest = app_mig.describe_file(path)
    entry = app_mig.record(tmp_path / "ledger", path, digest, [], None)

    assert json.loads(entry.read_text(encoding="utf-8"))["sha256"] == \
        hashlib.sha256(LF_SQL).hexdigest()


def test_the_identity_function_is_the_only_definition_of_it():
    """Three call sites hashed bytes for themselves before, and they disagreed: the
    record was written from one reading and compared against another. A fourth would
    disagree again, so the tool may hold exactly one definition."""
    hashing = [line.strip() for line in SOURCE.splitlines() if "hashlib.sha256" in line]

    assert hashing == ["return hashlib.sha256(canonical_bytes(raw)).hexdigest()"], (
        "every digest must come from digest_of()/file_digest(), so that the mode "
        f"writing a record and the mode reading it cannot drift apart: {hashing}")


def _glob_patterns() -> list[tuple[str, bool]]:
    """Every pattern this ledger globs over the migrations directory, and whether it
    reads the declared constant.

    Scoped to the receiver spelled `migrations`, deliberately: the same module globs a
    *snapshot* directory for `*.tar.gz` archives, which are not migrations and whose
    bytes no per-file identity covers. A glob over anything else is therefore not this
    guard's business — but a `migrations` glob it cannot evaluate is an assertion
    failure rather than a silent omission, so the reader cannot quietly stop covering
    the globs it was written for.
    """
    found: list[tuple[str, bool]] = []
    for node in ast.walk(ast.parse(SOURCE)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "glob" and node.args):
            continue
        receiver = node.func.value
        if not (isinstance(receiver, ast.Name) and receiver.id == "migrations"):
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            found.append((argument.value, False))
        elif isinstance(argument, ast.JoinedStr):
            via = any(isinstance(part, ast.FormattedValue)
                      and isinstance(part.value, ast.Name)
                      and part.value.id == "MIGRATION_SUFFIX"
                      for part in argument.values)
            pattern = "".join(
                part.value if isinstance(part, ast.Constant) else
                getattr(app_mig, getattr(part.value, "id", ""), "")
                for part in argument.values)
            found.append((pattern, via))
        else:
            raise AssertionError(f"a glob this guard cannot read: {ast.dump(argument)}")

    assert found, (
        "no glob over `migrations` found — either the ledger stopped discovering "
        "migrations or it renamed the directory, and this guard is now reading nothing")
    return found


def _globbed_suffixes() -> set[str]:
    return {Path(pattern).suffix for pattern, _ in _glob_patterns()}


def _attributes_in(repo: Path, path: str, *names: str) -> dict[str, str]:
    """What git says about a path, resolved the way a checkout resolves it.

    `git check-attr` rather than a reader of our own: the answer is the *last*
    matching line of a file a user's global config and `info/attributes` can also feed,
    and a hand-rolled matcher is how a guard ends up agreeing with itself while the
    checkout disagrees with it.
    """
    done = subprocess.run(["git", "-C", str(repo), "check-attr", *names, "--", path],
                          capture_output=True, text=True, check=False, env=git_env())
    assert done.returncode == 0, (
        f"git check-attr could not be asked ({done.returncode}): {done.stderr.strip()}")
    return {name: value for _, name, value in
            (line.split(": ", 2) for line in done.stdout.splitlines() if line.strip())}


def test_the_ledger_globs_only_the_extension_it_declares():
    """One extension, named once. A literal glob is invisible to the constant, so a
    change to the constant would leave a glob behind still looking for the old
    extension — and the pin is checked against the one that is declared."""
    patterns = _glob_patterns()

    assert {Path(pattern).suffix for pattern, _ in patterns} == {app_mig.MIGRATION_SUFFIX}, (
        "a glob looks for an extension the ledger does not declare — and therefore for "
        "one whose line endings nothing pins")
    assert all(via for _, via in patterns), (
        f"an extension is written into a glob instead of `MIGRATION_SUFFIX`, which is "
        f"then not the only place it is named: {patterns}")


def test_every_extension_the_ledger_identifies_files_by_is_pinned_to_lf():
    """The ledger folds line endings now, so a CRLF checkout no longer *lies* — but the
    file is still two different files on two platforms, and the attribute is what
    decides which one the repository stores. This pin was missing for `*.sql`: 27 of
    the 73 migrations in `supabase/migrations` were materialised CRLF here while the
    box read the LF the blobs hold."""
    assert app_mig.MIGRATION_SUFFIX.startswith("."), "an extension, not a bare name"

    for suffix in sorted(_globbed_suffixes()):
        attrs = _attributes_in(ROOT, f"supabase/migrations/0000_example{suffix}",
                               "text", "eol")
        assert attrs["eol"] == "lf", (
            f"`{suffix}` is hashed by deploy/apply_migration.py, and `.gitattributes` "
            f"leaves its line endings to `core.autocrlf` (eol: {attrs['eol']}): one "
            "unchanged file would be two files, and which one a checkout holds would "
            "depend on the machine. Add `<pattern> text eol=lf`.")
        assert attrs["text"] in ("set", "auto"), (
            f"`{suffix}` is pinned to eol=lf with text={attrs['text']}: `eol` is only "
            "honoured for a path git treats as text, so the pin does nothing.")


def test_the_pin_check_can_fail(tmp_path):
    """A guard that cannot fail is not a guard. The same reading, against a repository
    that pins one extension and leaves the ledger's to `core.autocrlf`, must say so —
    and a later line overriding the pin must be seen too, because that is the one way
    a written rule stops being the rule."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert subprocess.run(["git", "-C", str(repo), "init", "-q"], env=git_env(),
                          check=False).returncode == 0

    (repo / ".gitattributes").write_bytes(b"*.py text eol=lf\n")
    assert _attributes_in(repo, "x.py", "text", "eol") == {"text": "set", "eol": "lf"}
    assert _attributes_in(repo, "x.sql", "text", "eol") == {"text": "unspecified",
                                                             "eol": "unspecified"}

    # A later line wins, per attribute: a pin that a rule further down undoes is not
    # a pin, and the guard must read the attribute the way a checkout resolves it.
    (repo / ".gitattributes").write_bytes(b"*.sql text eol=lf\n*.sql text eol=crlf\n")
    assert _attributes_in(repo, "x.sql", "text", "eol")["eol"] == "crlf"

    # `-text` leaves `eol` meaningless, which the guard's second assertion answers for.
    (repo / ".gitattributes").write_bytes(b"*.sql -text\n")
    assert _attributes_in(repo, "x.sql", "text", "eol") == {"text": "unset",
                                                             "eol": "unspecified"}


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


# ── which archive can undo the migration: the one that does not already have it ─
#
# `064_seb_door_refusals.sql` was applied on 10 October and applied again on 12
# October, to prove it idempotent. The re-run took its own snapshot first, and by then
# the table the file creates was already in the schema — so the box held three
# archives carrying that one stem and the **newest** of them was the one that could not
# undo anything: restoring it leaves `seb_door_refusal` exactly where it is, under a
# record calling it the way back. The archive is read here, not just its name.

def _snapshot(out_dir: Path, name: str, tables) -> Path:
    """A real snapshot archive: a member per table, and the manifest the tool reads.

    Written rather than faked, because the decision is made from the manifest and a
    fixture of `b"archive-bytes"` would only measure its own emptiness.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / name
    # `tables=None` writes a manifest that does not say which tables it carries, which
    # is not the same thing as one that carries none — see the guard below.
    body = {"label": name, "kind": "postgrest-logical-snapshot"}
    if tables is not None:
        body["tables"] = {table: {"rows": 0, "sha256": "0" * 64} for table in tables}
    manifest = json.dumps(body).encode("utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        for table in (tables or []):
            blob = b""
            info = tarfile.TarInfo(f"tables/{table}.ndjson")
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    return archive


WIDGET_SQL = "CREATE TABLE widget (id uuid);\n"
WIDGET = [("table", "table widget", 0)]
COLUMN_ONLY = [("column", "column widget.id", 0)]
OLD_ARCHIVE = "scangrade-db-20261010T034327Z-064_widget.tar.gz"


def _recorded(ledger: Path, stem: str = "064_widget"):
    return json.loads((ledger / f"{stem}.json").read_text(encoding="utf-8"))


def test_a_snapshot_that_already_carries_the_table_cannot_undo_it(tmp_path):
    after = _snapshot(tmp_path, "scangrade-db-20261012T112256Z-064_widget.tar.gz",
                      ["widget"])
    before = _snapshot(tmp_path, OLD_ARCHIVE, [])

    assert app_mig.can_undo(after, WIDGET) is False, (
        "a snapshot taken after the migration still carries the table it creates, and "
        "restoring it leaves the migration exactly where it was")
    assert app_mig.can_undo(before, WIDGET) is True
    assert app_mig.can_undo(before, COLUMN_ONLY) is None, (
        "a manifest lists tables and nothing else, so a file that creates no table "
        "has no answer here — 'cannot say' is not a refusal")
    junk = tmp_path / "hand-copied.tar.gz"
    junk.write_bytes(b"archive-bytes")
    assert app_mig.can_undo(junk, WIDGET) is None, (
        "an archive whose manifest cannot be read has no verdict to give, and the "
        "name rule stands for it exactly as before")


def test_a_manifest_that_does_not_list_tables_cannot_say(tmp_path):
    """A snapshot whose manifest holds no table list is not a snapshot holding none:
    reading it as empty would have `can_undo` call every such archive able, which is
    exactly the claim this section exists to stop making."""
    quiet = _snapshot(tmp_path, "scangrade-db-20261012T112256Z-064_widget.tar.gz",
                      None)

    assert app_mig.snapshot_tables(quiet) is None
    assert app_mig.can_undo(quiet, WIDGET) is None


def test_the_archive_that_can_undo_it_wins_over_the_newest_name(tmp_path):
    out = tmp_path / "backups"
    _snapshot(out, OLD_ARCHIVE, [])
    _snapshot(out, "scangrade-db-20261012T111743Z-064_widget.tar.gz", ["widget"])
    _snapshot(out, "scangrade-db-20261012T112256Z-064_widget.tar.gz", ["widget"])
    path = tmp_path / "064_widget.sql"
    path.write_text(WIDGET_SQL, encoding="utf-8")

    chosen = app_mig.undoing_archive(out, path, WIDGET)

    assert chosen.name == OLD_ARCHIVE, (
        "the two newest archives with the stem were taken after the table existed, so "
        "they are the ones that cannot undo this migration")
    newest = app_mig.undoing_archive(out, path, COLUMN_ONLY)
    assert newest.name == "scangrade-db-20261012T112256Z-064_widget.tar.gz", (
        "when nothing can say, the newest name still stands")
    assert app_mig.undoing_archive(out / "absent", path, WIDGET) is None


def _commit_setup(monkeypatch, tmp_path, *, taken_tables, archives=(), on_record=None,
                  sql=WIDGET_SQL, name="064_widget.sql"):
    """Drive a real `--commit` run, with the snapshot and the catalogue scripted."""
    repo = tmp_path / "repo"
    migrations = repo / "supabase" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / name).write_text(sql, encoding="utf-8")
    out = tmp_path / "backups"
    for archive_name, tables in archives:
        _snapshot(out, archive_name, tables)
    taken = _snapshot(out, f"scangrade-db-20261012T112256Z-{Path(name).stem}.tar.gz",
                      taken_tables)
    ledger = tmp_path / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    if on_record is not None:
        (ledger / f"{Path(name).stem}.json").write_text(
            json.dumps({"file": name, "sha256": "x", "recovery_point": on_record}),
            encoding="utf-8")

    monkeypatch.setattr(app_mig, "load_migration_url", lambda repo: "postgresql://x")
    monkeypatch.setattr(app_mig, "check_target", lambda url, repo: REF)
    monkeypatch.setattr(app_mig, "connect", lambda url: FakeConn())
    monkeypatch.setattr(app_mig, "schema_snapshot", lambda cur: {"table widget": "x"})
    monkeypatch.setattr(app_mig, "dry_run", lambda url, sql, require_idempotent: [])
    monkeypatch.setattr(app_mig, "take_recovery_point",
                        lambda url, repo, out_dir, keep, label: taken)
    monkeypatch.setattr(sys, "argv", [
        "apply_migration.py", str(migrations / name), "--commit",
        "--repo", str(repo), "--out", str(out), "--ledger", str(ledger)])
    return taken, out, ledger


def test_the_snapshot_is_named_when_it_can_undo_the_migration(monkeypatch, tmp_path):
    """The ordinary run: the snapshot was taken *before* the file ran, so it is the
    recovery point and nothing is written about why."""
    taken, _out, ledger = _commit_setup(monkeypatch, tmp_path, taken_tables=[])

    assert app_mig.main() == 0

    payload = _recorded(ledger)
    assert payload["recovery_point"] == str(taken)
    assert "recovery_point_basis" not in payload, (
        "the run that applies a migration takes the right archive by construction, so "
        "there is nothing to explain")


def test_a_reapply_names_the_archive_that_can_undo_it_not_the_snapshot_it_took(
        monkeypatch, tmp_path, capsys):
    taken, out, ledger = _commit_setup(
        monkeypatch, tmp_path, taken_tables=["widget"], archives=[(OLD_ARCHIVE, [])],
        on_record=str(tmp_path / "backups" / OLD_ARCHIVE))

    assert app_mig.main() == 0

    payload = _recorded(ledger)
    assert payload["recovery_point"] == str(out / OLD_ARCHIVE), (
        "the re-run's snapshot already carries `widget`, so naming it claims a "
        "restoration that leaves the migration in place")
    assert payload["recovery_point"] != str(taken)
    assert "widget" in payload["recovery_point_basis"]
    assert "already named" in payload["recovery_point_basis"]
    assert "NOT naming the snapshot this run took" in capsys.readouterr().out


def test_a_reapply_falls_back_to_the_files_own_older_archive(monkeypatch, tmp_path):
    """No record yet, but the box still holds the archive the first run took: that is
    the one that can undo the migration, and it is the one the record names."""
    _taken, out, ledger = _commit_setup(
        monkeypatch, tmp_path, taken_tables=["widget"], archives=[(OLD_ARCHIVE, [])])

    assert app_mig.main() == 0

    payload = _recorded(ledger)
    assert payload["recovery_point"] == str(out / OLD_ARCHIVE)
    assert "taken before it" in payload["recovery_point_basis"]


def test_a_reapply_with_no_archive_that_can_undo_it_names_none(monkeypatch, tmp_path):
    """Nothing on the box predates the table, so the record says so — an empty path is
    honest and a wrong one is what a recovery gets spent on."""
    _taken, _out, ledger = _commit_setup(monkeypatch, tmp_path, taken_tables=["widget"])

    assert app_mig.main() == 0

    payload = _recorded(ledger)
    assert payload["recovery_point"] is None
    assert "no archive that can" in payload["recovery_point_basis"]


def test_a_record_naming_an_archive_proved_unable_is_not_kept(monkeypatch, tmp_path):
    """The record's own archive is preferred only while it can still undo the file: a
    snapshot taken after the migration is not made true by having been recorded."""
    stale = _snapshot(tmp_path / "backups", "scangrade-db-20261011T000000Z-064_widget.tar.gz",
                      ["widget"])
    _taken, out, ledger = _commit_setup(
        monkeypatch, tmp_path, taken_tables=["widget"], archives=[(OLD_ARCHIVE, [])],
        on_record=str(stale))

    assert app_mig.main() == 0

    payload = _recorded(ledger)
    assert payload["recovery_point"] == str(out / OLD_ARCHIVE), (
        "the archive the record named was taken after the table existed, and was not "
        "kept on the strength of being written down")


def test_a_record_naming_an_archive_outside_the_backup_dir_is_still_kept(
        monkeypatch, tmp_path):
    """The archive a record names is authoritative about *where* it is: an adopted one
    that was moved out of a scratch directory, or a box whose `--out` has since moved,
    leaves the record as the only thing that knows. Nothing that can undo the file is
    in the backup directory here, so the record's own archive is the answer."""
    elsewhere = _snapshot(tmp_path / "moved-here", OLD_ARCHIVE, [])
    taken, out, ledger = _commit_setup(
        monkeypatch, tmp_path, taken_tables=["widget"], on_record=str(elsewhere))

    assert app_mig.main() == 0

    payload = _recorded(ledger)
    assert Path(payload["recovery_point"]) == elsewhere, (
        "the record already named the archive that can undo this migration")
    assert str(out / OLD_ARCHIVE) != str(elsewhere), (
        "the fixture has to keep them apart or the test proves nothing")
    assert "already named" in payload["recovery_point_basis"]


def test_the_commit_path_decides_the_recovery_point_before_it_records_it():
    """The record is written from a decision about the archive, not from whatever the
    run happened to take, and the decision sits in the path that writes the record."""
    decided = _main_calls("recovery_point_to_name")
    recorded = _main_calls("record")

    assert decided and recorded
    assert max(decided) < min(recorded), (
        "the recovery point must be chosen before the record names it")
