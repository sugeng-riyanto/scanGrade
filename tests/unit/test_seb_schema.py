"""The SEB schema must be safe to run twice, and safe to run at all.

Four properties of `supabase/migrations/062_seb_and_environment_signals.sql`, and
each one is a way this change could do real harm rather than merely be wrong:

* **Safe to run twice.** These migrations are pasted into a SQL editor and also run
  through `deploy/apply_migration.py`; a file that is not idempotent fails *for real*
  on the second run, mid-release. The offline guard here is that every statement which
  creates something is guarded (`IF NOT EXISTS`, or `DROP ... IF EXISTS` first) and
  that the file owns no transaction — the runner owns it, and a file that commits makes
  a dry run lie.
* **Safe to run at all** — the one that matters most. `exams.require_seb` defaults to
  **false**, because Safe Exam Browser does not exist for Android or ChromeOS: switching
  this on for a paper that pupils sit from phones blocks the majority of the room, not a
  handful. A default of `true` would be a catastrophic default, so it is asserted here
  with the reason written down, and the migration has to keep saying why.
* **The server never mints the Browser Exam Key.** A BEK that a server could generate
  would prove nothing — it embeds the SEB application's code signature, and the SEB
  project states plainly that only SEB clients can produce one. So the column is
  nullable and carries no default: an empty column is the honest state of "no BEK has
  been registered", and a generated one would be a lie with a hash in it.
* **The weak signal cannot punish anyone.** `environment_signal` records probabilistic
  evidence — VM/remote-desktop hints that cheap, old, and legitimately virtualised
  machines all share. Nothing that charges a penalty, locks a sitting or moves a score
  may read it. That is checked by scanning those modules for the table's name, so the
  invariant survives a later phase that is tempted to wire it in.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (ROOT / "supabase" / "migrations"
             / "062_seb_and_environment_signals.sql")
SQL = MIGRATION.read_text(encoding="utf-8")

#: The tables this migration is responsible for.
TABLES = ("environment_signal", "exam_seb_credential", "seb_access_log")

#: Modules that charge a penalty, lock a sitting, or move a score. A weak signal
#: must be readable by *none* of them.
PENALTY_MODULES = (
    "app/services/anti_cheat_service.py",
    "app/services/resume_code.py",
    "app/services/exam_scoring.py",
    "app/routes/api.py",
    "app/routes/student.py",
)


def _statements() -> list[str]:
    """Every `;`-terminated statement, with comment lines removed.

    Comments are stripped first because both `COMMENT ON ...` and a doc line can
    mention a table name, and a guard that reads prose fires on a sentence.
    """
    body = "\n".join(line for line in SQL.splitlines()
                     if not line.lstrip().startswith("--"))
    return [part.strip() for part in body.split(";") if part.strip()]


class TestTheMigrationIsSafeToRunTwice:
    def test_it_exists_and_is_not_empty(self):
        assert MIGRATION.is_file(), "the SEB migration is missing"
        assert len(SQL) > 2000, "the migration is a stub, not a schema"

    def test_every_create_is_guarded(self):
        for statement in _statements():
            head = statement.lstrip().upper()
            if head.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX")):
                assert "IF NOT EXISTS" in statement.upper(), (
                    "an unguarded CREATE makes the second run fail:\n" + statement[:160])

    def test_every_column_add_is_guarded(self):
        for statement in _statements():
            if re.match(r"(?is)^ALTER TABLE .*ADD COLUMN", statement):
                assert "IF NOT EXISTS" in statement.upper(), (
                    "an unguarded ADD COLUMN makes the second run fail")

    def test_every_policy_is_dropped_before_it_is_created(self):
        for statement in _statements():
            if statement.lstrip().upper().startswith("CREATE POLICY"):
                name = re.search(r"CREATE POLICY\s+\"?([\w.]+)\"?", statement)
                assert name, "a policy whose name cannot be read cannot be dropped"
                drop = f'DROP POLICY IF EXISTS "{name.group(1)}"'
                assert drop in SQL, (
                    f"{name.group(1)} is created without a DROP POLICY IF EXISTS "
                    "first, so the second run aborts on a duplicate policy")

    def test_the_file_owns_no_transaction(self):
        """The runner owns it; a file that commits makes the dry run a lie."""
        assert not re.search(r"(?mi)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", SQL), (
            "the migration must not manage its own transaction")


class TestTheSwitchCannotBlockAPupilByDefault:
    def test_require_seb_defaults_to_false(self):
        match = re.search(
            r"(?is)ALTER TABLE exams ADD COLUMN IF NOT EXISTS require_seb .*?;", SQL)
        assert match, "the migration no longer adds exams.require_seb"
        statement = match.group(0).upper()
        assert "BOOLEAN" in statement and "NOT NULL" in statement
        assert "DEFAULT FALSE" in statement, (
            "require_seb must default to false: SEB has no Android/ChromeOS build, so "
            "a default of true blocks every pupil on a phone")

    def test_the_reason_is_written_down_where_the_column_is(self):
        """The next reader has to meet the reason, not a rule."""
        assert "Android" in SQL and "ChromeOS" in SQL, (
            "the migration must say which platforms SEB cannot serve")
        assert "memblokir total mayoritas murid" in SQL or "blocks the majority" in SQL


class TestThereIsNoBrowserExamKeyAtAll:
    """The correction that shapes this feature, and it is enforced by *absence*.

    Rewritten from the shape this class had before, and the rewrite is stricter
    rather than looser: it used to require a `browser_exam_key` column kept nullable
    and never populated. The BEK hashes the config **and the SEB binary's own
    signature**, so it differs per version and platform and cannot be computed by a
    server — the only road would be collecting a registered key per platform, which is
    precisely the complexity the Config Key removes. A column that does not exist
    cannot be filled in by a mistaken writer; a nullable one only documents that
    nobody should. So the assertion is now that the column is gone.
    """

    def test_no_bek_column_exists_anywhere(self):
        assert not re.search(r"(?im)^\s*browser_exam_key\b", SQL), (
            "a `browser_exam_key` column is back. It cannot be produced server-side, so "
            "its existence only invites a writer to try — the Config Key replaces it, "
            "and absence is the enforcement.")

    def test_the_servers_own_key_is_the_config_key_on_the_exam(self):
        assert re.search(
            r"(?is)ALTER TABLE exams ADD COLUMN IF NOT EXISTS seb_config_key\b", SQL), \
            "exams.seb_config_key is missing"
        assert not re.search(r"(?im)^\s*config_key\s+(TEXT|UUID|JSONB)", SQL), (
            "the Config Key is declared a second time. It belongs to the *exam* — one "
            "value for the whole class — and two homes is how two values start to "
            "differ, with the stale one looking correct until a pupil cannot open "
            "their paper.")

    def test_the_migration_records_why_config_key_replaces_the_bek(self):
        """The next reader has to meet the reason, not just the shape."""
        assert "Browser Exam Key" in SQL, (
            "the file must name the thing it is replacing")
        assert "dihitung server" in SQL, (
            "the file must record that the BEK cannot be computed server-side")
        assert "Config Key" in SQL and "platform" in SQL, (
            "and that the Config Key is the value that holds across platforms")


class TestTheWeakSignalIsNotPunitive:
    def test_no_penalty_module_reads_the_signal_table(self):
        offenders = []
        for rel in PENALTY_MODULES:
            path = ROOT / rel
            if not path.is_file():
                continue
            if "environment_signal" in path.read_text(encoding="utf-8"):
                offenders.append(rel)
        assert not offenders, (
            "a module that charges a penalty, locks a sitting or moves a score reads "
            f"environment_signal: {offenders}. The signal is probabilistic and must "
            "never trigger an automatic action — it is context for a human.")

    def test_the_migration_states_the_non_punitive_rule(self):
        assert "NON-PUNITIF" in SQL or "non-punitive" in SQL.lower(), (
            "the non-punitive rule belongs next to the table, not only in a phase note")
        assert "probabilistik" in SQL.lower()

    def test_no_scoring_threshold_is_published_in_the_public_copy(self):
        """The weights live in the schema and the server, never in a public page."""
        page = ROOT / "app" / "templates" / "guide"
        if page.is_dir():
            for path in page.rglob("*.html"):
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                assert "environment_signal" not in text, (
                    f"{path.name} names the signal table in a page a pupil can read")
