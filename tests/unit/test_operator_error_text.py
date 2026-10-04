"""Which third-party error texts reach an operator, and which are transient.

`failure.py` already answers *how* to show a failure (its own words, a lost-reply
sentence, a server refusal, or the raw text as a last resort). This file is the
other half: the *decisions* from the audit in `docs/ERROR_TEXT_AUDIT.md`, held so
they cannot drift.

Two kinds of decision live here:

* **The classifications.** GoTrue's transient create answers are already retried
  (`auth_retry`) and now recorded (`auth_health`); the point of this file is that
  when one reaches an operator it is shown in the creator's own words, never as
  GoTrue's generic "Database error creating new user". A transport failure keeps
  its "outcome unknown" sentence; a PostgREST refusal names the code.
* **The call sites that had fallen back to raw text.** The account importers and
  the super-admin password repair are all account-creation surfaces, and all three
  showed `str(e)` where the shared creator already carries a `user_message`.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "app" / "routes" / "admin.py"
ADMIN_SEKOLAH = ROOT / "app" / "routes" / "admin_sekolah.py"
SUPER_ADMIN = ROOT / "app" / "routes" / "super_admin.py"


# ── the classifications ──────────────────────────────────────────────────────

class TestTheTransientGoTrueClass:
    def test_a_transient_create_is_shown_in_the_creators_own_words(self):
        from app.utils import auth_retry, failure

        exc = auth_retry.AccountNotCreated(
            RuntimeError("Database error creating new user"))
        sentence = failure.sentence(exc)
        assert "Database error creating new user" not in sentence, (
            "the operator is shown GoTrue's generic text instead of the "
            "creator's sentence")
        assert "akun" in sentence.lower()

    def test_the_two_goTrue_create_answers_are_both_transient(self):
        from app.utils import auth_retry

        assert auth_retry.is_transient(RuntimeError("Database error creating new user"))
        assert auth_retry.is_transient(RuntimeError("Database error saving new user"))
        # …and a deterministic refusal is not, or a retry turns one account into a
        # loop of "already registered".
        assert not auth_retry.is_transient(RuntimeError("email already registered"))

    def test_a_lost_reply_says_the_outcome_is_unknown_not_failed(self):
        import httpx

        from app.utils import failure

        sentence = failure.sentence(httpx.ConnectError("Server disconnected"))
        assert sentence == failure.LOST_REPLY
        assert "Server disconnected" not in sentence

    def test_a_server_refusal_names_the_code(self):
        from postgrest.exceptions import APIError

        from app.utils import failure

        exc = APIError({"message": "invalid input", "code": "22P02"})
        sentence = failure.sentence(exc)
        assert failure.SERVER_REFUSED in sentence and "22P02" in sentence


# ── the call sites that fell back to raw text ────────────────────────────────

class TestAccountSurfacesShowTheCreatorsWords:
    def test_the_legacy_import_row_errors_are_classified(self):
        src = ADMIN.read_text(encoding="utf-8-sig")
        assert "Baris {row_idx} ({full_name}): {failure.sentence(e)}" in src, (
            "a legacy import row still prints the raw exception text")
        assert "Baris {row_idx} ({full_name}): {e}" not in src

    def test_the_school_importer_row_errors_are_classified(self):
        src = ADMIN_SEKOLAH.read_text(encoding="utf-8-sig")
        assert "Baris {row_idx}: {failure.sentence(e)}" in src, (
            "the subject import row still prints the raw exception text")
        assert "Gagal membaca daftar mapel: {failure.sentence(e)}" in src
        assert "Baris {row_idx}: {e}" not in src

    def test_the_super_admin_auth_repair_is_classified(self):
        src = SUPER_ADMIN.read_text(encoding="utf-8-sig")
        assert '"error": str(e)[:60]' not in src, (
            "the demo-password repair still shows GoTrue's raw text for a failed "
            "password update")
        assert "failure.sentence(e)" in src
