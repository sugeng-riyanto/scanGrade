"""Offline contract for the ScanMeet readiness probe; never connects to production."""
from pathlib import Path
from deploy import scanmeet_migration_readiness as gate


def test_all_three_migrations_are_required(tmp_path):
    assert len(gate.files_ready(tmp_path)) == 3
    migrations = tmp_path / "supabase" / "migrations"
    migrations.mkdir(parents=True)
    for path in gate.REQUIRED.values():
        (migrations / path).write_text("-- fixture")
    assert gate.files_ready(tmp_path) == []


def test_offline_check_does_not_claim_live_database(capsys):
    assert gate.main(["--repo", str(Path(__file__).resolve().parents[2])]) == 0
    output = capsys.readouterr().out
    assert "OFFLINE ONLY" in output
    assert "not database readiness" in output


def test_database_failures_are_reported_fail_closed():
    """The probe is judged against a cursor that answers like the real server.

    `connect_readonly()` sets ``autocommit=True``, and PostgreSQL refuses a
    savepoint outside a transaction block (25P01). This stub used to *return*
    quietly for those statements, so a probe that leaned on one passed here and
    crashed on an operator's box — the stub is now the thing that would catch it.
    """

    class ReadOnlyAutocommitCursor:
        def execute(self, sql):
            statement = " ".join(sql.upper().split())
            if statement.startswith(("SAVEPOINT", "ROLLBACK TO SAVEPOINT", "RELEASE SAVEPOINT")):
                raise RuntimeError("SAVEPOINT can only be used in transaction blocks")
            raise RuntimeError("relation does not exist")

    findings = gate.database_findings(ReadOnlyAutocommitCursor())
    assert len(findings) >= 8
    assert all("query unavailable" in finding for finding in findings)


def test_the_probe_asks_for_no_transaction_block():
    """Every statement it sends is a query; none is transaction scaffolding."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(gate.database_findings)))
    literals = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert literals, "the probe must be made of statements"
    for literal in literals:
        assert "SAVEPOINT" not in literal.upper()


def test_no_mutating_sql_in_database_probe():
    import inspect
    source = inspect.getsource(gate.database_findings)
    for command in ("INSERT INTO", "UPDATE public", "DELETE FROM", "ALTER TABLE",
                    "CREATE TABLE", "DROP TABLE", "COMMIT"):
        assert command not in source
