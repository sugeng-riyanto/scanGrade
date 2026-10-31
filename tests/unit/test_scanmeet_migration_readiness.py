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
    class BrokenCursor:
        def execute(self, sql):
            if sql.startswith("SAVEPOINT") or sql.startswith("ROLLBACK TO") or sql.startswith("RELEASE"):
                return
            raise RuntimeError("relation does not exist")
    findings = gate.database_findings(BrokenCursor())
    assert len(findings) >= 8
    assert all("query unavailable" in finding for finding in findings)


def test_no_mutating_sql_in_database_probe():
    import inspect
    source = inspect.getsource(gate.database_findings)
    for command in ("INSERT INTO", "UPDATE public", "DELETE FROM", "ALTER TABLE",
                    "CREATE TABLE", "DROP TABLE", "COMMIT"):
        assert command not in source
