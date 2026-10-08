"""The membership-request schema must be safe to run twice, and must not leak.

Migration 063 adds a way for a teacher to ask another school to take them on, plus
the record of what they agreed to and the means to close their access completely.
The guards here are the properties that would be *expensive* to get wrong rather
than merely wrong:

* **Safe to run twice** — these files are pasted into a SQL editor and also run
  through `deploy/apply_migration.py`; a non-idempotent file fails for real on the
  second run.
* **One pending request per (teacher, target school)** — a partial unique index, not
  a unique constraint, because a teacher refused today may ask again and their old
  row must survive. Without the index, a double-tap on the form opens two requests
  and the destination school answers the same question twice.
* **Only the three destination-school roles can decide** — enforced by a CHECK on
  `decided_role`, so a decision has to say what authority it was taken under.
* **The destination school cannot learn the teacher's other memberships** — and the
  way that is guaranteed is that the column does not exist. A guard asserting the
  *absence* of a column is unusual, and it is the strongest form available: a column
  that is merely never rendered leaks the moment someone adds it to a select.
* **A decision cannot be written with the public key** — no INSERT/UPDATE/DELETE
  policy on the request table, so a forged request from a browser cannot approve
  itself. The backend uses the service key and checks the role first.
* **Consent carries a version** — `document_version` is NOT NULL, so "ever agreed"
  without a record of *what* cannot be stored at all.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "063_school_membership_requests.sql"
SQL = MIGRATION.read_text(encoding="utf-8")

#: Migration 044's vocabulary, which must survive this one.
PRIOR_STATUSES = ("'active'", "'invited'", "'inactive'")


def _statements() -> list[str]:
    """Every `;`-terminated statement, comment lines removed.

    Comments come out first because a doc line can name a table or a role, and a
    guard that reads prose fires on a sentence.
    """
    body = "\n".join(line for line in SQL.splitlines()
                     if not line.lstrip().startswith("--"))
    return [part.strip() for part in body.split(";") if part.strip()]


class TestTheMigrationIsSafeToRunTwice:
    def test_it_exists_and_is_not_a_stub(self):
        assert MIGRATION.is_file()
        assert len(SQL) > 3000, "the migration is a stub, not a schema"

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

    def test_the_status_constraint_is_dropped_before_it_is_rebuilt(self):
        drop = "DROP CONSTRAINT IF EXISTS teacher_school_membership_status_check"
        assert drop in SQL, (
            "adding a value to a CHECK requires dropping it first, or the second run "
            "aborts on a duplicate constraint")
        assert SQL.index(drop) < SQL.index(
            "ADD CONSTRAINT teacher_school_membership_status_check"), (
            "the drop must precede the add")

    def test_every_policy_is_dropped_before_it_is_created(self):
        for statement in _statements():
            if statement.lstrip().upper().startswith("CREATE POLICY"):
                name = re.search(r"CREATE POLICY\s+\"?([\w.]+)\"?", statement)
                assert name, "a policy whose name cannot be read cannot be dropped"
                assert f'DROP POLICY IF EXISTS "{name.group(1)}"' in SQL, (
                    f"{name.group(1)} is created without a DROP POLICY IF EXISTS first")

    def test_the_file_owns_no_transaction(self):
        assert not re.search(r"(?mi)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", SQL), (
            "the runner owns the transaction; a file that commits makes a dry run lie")


class TestTheStatusVocabularyKeepsTheOldValues:
    def test_prior_statuses_survive(self):
        match = re.search(r"(?is)ADD CONSTRAINT teacher_school_membership_status_check"
                          r".*?;", SQL)
        assert match, "the status constraint is no longer rebuilt"
        rebuilt = match.group(0)
        for value in PRIOR_STATUSES:
            assert value in rebuilt, (
                f"{value} was dropped from the status vocabulary — rows already holding "
                "it would become invalid")

    def test_closed_is_added(self):
        assert "'closed'" in SQL, "offboarding needs a status that means closed"


class TestOnePendingRequestPerPair:
    def test_the_index_is_partial_on_pending(self):
        match = re.search(
            r"(?is)CREATE UNIQUE INDEX IF NOT EXISTS"
            r" idx_school_membership_request_pending_once.*?;", SQL)
        assert match, "the single-pending-request index is missing"
        index = re.sub(r"\s+", " ", match.group(0))
        assert "teacher_id, target_school_id" in index, (
            "the pending rule is per (teacher, target school)")
        assert "WHERE status = 'pending'" in index, (
            "the rule must be partial: a teacher refused today can ask again, and their "
            "old row has to survive")

    def test_the_queue_and_history_lookups_are_indexed(self):
        for name in ("idx_school_membership_request_school_queue",
                     "idx_school_membership_request_teacher",
                     "idx_school_membership_request_expiry"):
            assert name in SQL, f"{name} is missing; the page would scan"


class TestOnlyTheThreeDestinationRolesCanDecide:
    def test_the_check_names_exactly_those_three(self):
        match = re.search(r"(?is)decided_role TEXT\s*CONSTRAINT[^,]*CHECK \((.*?)\)\s*,", SQL)
        assert match, "decided_role has no CHECK"
        for role in ("'admin_sekolah'", "'principal'", "'vice_principal'"):
            assert role in match.group(1), f"{role} cannot be recorded as an approver"
        for forbidden in ("'murid'", "'guru'", "'super_admin'"):
            assert forbidden not in match.group(1), (
                f"{forbidden} must not be able to decide a membership request")

    def test_a_non_decision_carries_no_role(self):
        """A cancelled or expired request was decided by nobody."""
        assert "decided_role IS NULL" in SQL, (
            "the CHECK must allow NULL for a request that was cancelled or expired")


class TestTheDestinationSchoolCannotSeeOtherMemberships:
    def test_there_is_no_origin_school_column(self):
        """The privacy rule is enforced by the column not existing.

        A column that is merely never rendered leaks the moment someone adds it to a
        select; a column that is absent cannot be selected by mistake.
        """
        banned = ("origin_school_id", "from_school_id", "requester_school_id",
                  "home_school_id", "source_school_id")
        for name in banned:
            assert name not in SQL, (
                f"`{name}` would let the destination school learn where the teacher "
                "already works — the request must not carry an origin school")

    def test_no_policy_lets_the_destination_school_read_membership_rows(self):
        """`teacher_school_membership` is not opened to the destination school here."""
        for statement in _statements():
            if statement.lstrip().upper().startswith("CREATE POLICY"):
                assert "teacher_school_membership" not in statement, (
                    "a policy on teacher_school_membership would let the destination "
                    "school enumerate the teacher's other memberships")


class TestADecisionCannotBeForgedWithThePublicKey:
    def test_there_is_no_write_policy_on_the_request_table(self):
        for statement in _statements():
            head = statement.lstrip().upper()
            if not head.startswith("CREATE POLICY"):
                continue
            on_request = "school_membership_request" in statement
            if on_request:
                assert "FOR SELECT" in statement.upper(), (
                    "only a SELECT policy may exist on the request table: an INSERT or "
                    "UPDATE policy lets a hand-made request approve itself")

    def test_the_migration_says_why(self):
        assert "service key" in SQL, (
            "the file must record that decisions are written server-side after the "
            "role check, not through the public key")


class TestConsentCarriesAVersion:
    def test_document_version_is_not_nullable(self):
        match = re.search(r"(?im)^\s*document_version .*$", SQL)
        assert match, "document_version is missing"
        assert "NOT NULL" in match.group(0).upper(), (
            "'ever agreed' without a version cannot be stored at all")

    def test_the_hash_is_recorded_too(self):
        assert "document_sha256" in SQL, (
            "the version says which document; the hash says which text — a document "
            "edited without a version bump is exactly the case needing proof")

    def test_one_consent_row_per_request(self):
        assert "idx_membership_consent_once" in SQL, (
            "the evidence for one request must be a single row, not an append log")


class TestTheAsymmetryIsDocumented:
    def test_reopen_is_documented_as_deliberate(self):
        assert "HANYA admin_sekolah" in SQL, (
            "the reopen asymmetry must be written down or it will be 'fixed' later")
        assert "bukan bug" in SQL.lower() or "keputusan produk" in SQL, (
            "the file has to say the asymmetry is intentional")
