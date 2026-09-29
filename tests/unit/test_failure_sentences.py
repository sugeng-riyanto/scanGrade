"""What a person is told when an action did not go through.

Reported live: ``https://scangrade.web.id/admin-sekolah/profile`` answers
``Gagal: Server disconnected``. That string is ``httpx``'s, and it arrived through the
shape every admin page ends its write with::

    except Exception as e:
        flash(f"Gagal: {e}", "error")

The transport half of that fault now has a recovery (a dropped reply is settled by a
read — see ``supabase_retry``), but the *sentence* was never the page's to begin with:
34 call sites across six route modules interpolated whatever the exception happened to
be, so an operator read a Python library's words, a PostgREST JSON body, or ``str(e)``
of a sentence somebody else already wrote for them.

``app/utils/failure.py`` owns that decision, the way ``denials.py`` owns the tone of a
refusal, and these tests hold both halves of it:

* the three classes a person can act on get a sentence — the app's own ``user_message``,
  a lost reply that says the outcome is unknown and what to do next, and a server answer
  that keeps its code for support;
* an **unknown** failure keeps the exception's own text, on one line and bounded,
  because a sentence invented for it would hide the only clue there is;
* and the sweep: no page flashes an exception at a person any more — with a check that
  the sweep is reading something, because a rule about "nothing anywhere" passes by
  accident the moment it stops looking.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
from postgrest.exceptions import APIError

from app.errors import NotFoundError, ValidationError
from app.services.school_officials import OfficialError
from app.utils import failure

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"

#: A flash that hands the reader the exception itself. The brace forms are anchored so
#: ``{error.error_code}`` — a field a page is entitled to show — is not an offender.
RAW_FLASH = re.compile(r"\{e\}|\{exc\}|\{err\}|\{error\}|str\(e\)|str\(exc\)|str\(err\)|"
                       r"getattr\(e\b")


def dropped() -> httpx.RemoteProtocolError:
    """The exact exception the live project raises, not a generic one."""
    return httpx.RemoteProtocolError("Server disconnected")


# ── 1. the app's own errors are shown in their own words ─────────────────────

class TestTheAppsOwnErrorsKeepTheirWords:

    def test_a_validation_error_shows_the_field_and_the_reason(self):
        got = failure.sentence(ValidationError("nisn", "wajib diisi"))
        assert got == "nisn: wajib diisi", got

    def test_a_not_found_error_shows_its_sentence(self):
        got = failure.sentence(NotFoundError("Guru", "t-1"))
        assert got == "Guru tidak ditemukan.", got

    def test_a_service_error_that_brought_its_own_sentence_keeps_it(self):
        got = failure.sentence(OfficialError("Akun kepala sekolah gagal dibuat."))
        assert got == "Akun kepala sekolah gagal dibuat.", got

    def test_the_technical_message_is_not_what_a_person_reads(self):
        got = failure.sentence(ValidationError("nisn", "wajib diisi"))
        assert "Validation failed" not in got
        assert "nisn: wajib diisi" in got


# ── 2. a lost reply says so, and says what to do ─────────────────────────────

class TestALostReply:

    def test_the_library_name_never_reaches_the_reader(self):
        got = failure.sentence(dropped())
        assert "disconnected" not in got.lower(), got
        assert "httpx" not in got.lower(), got

    def test_it_says_the_outcome_is_not_known(self):
        """A dropped connection does not say whether the statement ran — and for an
        insert that is the difference between one row and two."""
        got = failure.sentence(dropped())
        assert "belum bisa dipastikan" in got or "tidak bisa dipastikan" in got, got

    def test_it_names_the_next_step(self):
        got = failure.sentence(dropped())
        assert "Muat ulang" in got or "muat ulang" in got, got
        assert "periksa" in got.lower(), got


# ── 3. a server answer is a sentence with the code kept ──────────────────────

class TestAServerAnswer:

    def test_the_json_body_is_not_quoted_at_the_reader(self):
        got = failure.sentence(APIError({"message": 'column "foo" does not exist',
                                         "code": "42703"}))
        assert "column" not in got, got
        assert "{" not in got and "}" not in got, got

    def test_the_code_survives_so_support_can_act(self):
        got = failure.sentence(APIError({"message": "x", "code": "42703"}))
        assert "42703" in got, got

    def test_an_api_error_without_a_code_still_reads(self):
        got = failure.sentence(APIError({"message": "x"}))
        assert got and "()" not in got, got


# ── 4. an unknown failure keeps the only clue there is ───────────────────────

class TestAnUnknownFailure:

    def test_it_keeps_the_exception_text(self):
        assert "disk penuh" in failure.sentence(RuntimeError("disk penuh"))

    def test_it_is_one_bounded_line(self):
        got = failure.sentence(RuntimeError("baris satu\nbaris dua " + "x" * 5000))
        assert "\n" not in got, "a flash with a newline renders as a broken toast"
        assert len(got) <= failure.UNKNOWN_LIMIT + 1, len(got)
        assert got.endswith("…"), "the text was cut without saying so"


# ── 5. no page hands the reader an exception ─────────────────────────────────

def flash_lines():
    """Every ``flash(...)`` call in app/, with the file and line it sits on."""
    found = []
    for path in sorted(APP.rglob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "flash(" in line:
                found.append((path, i, line))
    return found


class TestNoPageFlashesAnExceptionAtAPerson:

    def test_no_flash_interpolates_the_exception(self):
        offenders = [f"{p.relative_to(ROOT)}:{i}  {line.strip()[:90]}"
                     for p, i, line in flash_lines() if RAW_FLASH.search(line)]
        assert not offenders, (
            "a page hands the reader the exception itself — app/utils/failure.py is the "
            "one place that decides what a person is told:\n  " + "\n  ".join(offenders))

    def test_the_sweep_is_reading_something(self):
        """A rule about "nothing anywhere" passes by accident when it stops looking."""
        scanned = list(APP.rglob("*.py"))
        assert len(scanned) > 100, f"the sweep opened only {len(scanned)} files"
        assert flash_lines(), "no flash call was found anywhere, so the sweep proves nothing"

    def test_the_sentence_helper_is_what_the_pages_call(self):
        callers = {p for p, _i, _line in
                   ((p, i, line) for p, i, line in flash_lines())
                   if "failure.sentence(" in p.read_text(encoding="utf-8")}
        assert len(callers) >= 5, (
            f"only {len(callers)} module(s) call failure.sentence — an empty sweep is "
            "what a deleted flash looks like")
