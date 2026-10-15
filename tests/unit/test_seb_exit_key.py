"""The exit key: the one thing a locked pupil needs, and the two ways it stopped working.

Measured in production, 15 October 2026 — and this file exists because the first
diagnosis was wrong.
-------------------------------------------------------------------------------
A pupil sat a gated paper in real SEB and could not leave it. The report was "the
quit password the system generates cannot be used to exit", and the natural reading
was that the hash in the `.seb` file was computed the wrong way. **It was not**, and
the evidence is in this file:

* SEB for Windows computes it in
  ``SafeExamBrowser.Configuration/Cryptography/HashAlgorithm.cs`` as
  ``SHA256(Encoding.UTF8.GetBytes(password))`` rendered ``b.ToString("x2")``, and
  ``ClientResponsibility.IsValidQuitPassword`` compares with
  ``StringComparison.OrdinalIgnoreCase``. :func:`app.services.seb_crypto.sha256_hex`
  produces those same bytes — asserted below against the client's *own* unit-test
  vector, quoted verbatim, and separately against the installed SEB library on a
  Windows box (``deploy/seb_quit_probe.py``).
* SEB's own client log from that sitting records the client doing exactly that
  comparison, three times:
  ``[ShellResponsibility] The user entered the wrong quit password.`` So the dialog
  appeared, the hash was read, and what was typed did not hash to it.

Two defects, and they are the two halves this file guards
---------------------------------------------------------
1. **The panel showed one password in both cells.** `seb_panel.html` kept a single
   Alpine ``password`` variable for the quit *and* the admin cell while tracking
   visibility per kind, so revealing the second kind overwrote the first cell's
   text — a supervisor reading the box labelled "Quit password" could be reading
   the admin password, and SEB refuses it. Guarded here from the template's own
   text, because the defect *is* a binding.
2. **Nothing said that a re-issue voided the files already handed out.** Issuing
   replaces both passwords and writes a new Config Key over the stored one, so every
   distributed file stops matching — and `file_is_current` cannot see it, because
   the re-issue updates the very yardstick it compares against. The production
   artifacts show it plainly: the file a pupil downloaded carries
   `hashedQuitPassword dd111419…`, the credential row's hash is something else, and
   the row's `updated_at` is *later* than the download.

What this file does **not** claim
---------------------------------
That a GUI unlock was performed here. Launching SEB is a kiosk that takes the
machine's desktop, so the acceptance test is run against the **installed SEB code
itself** instead: `deploy/seb_quit_probe.py` asks `SafeExamBrowser.Configuration.dll`
to hash the disclosed password and compares it with the hash in the generated file.
The remaining step — typing it into a locked client — is a human one, and the report
says so.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import seb_crypto
from app.services import seb_service

ROOT = Path(__file__).resolve().parents[2]
PANEL = (ROOT / "app" / "templates" / "teacher" / "seb_panel.html").read_text(
    encoding="utf-8")
ROUTE = (ROOT / "app" / "routes" / "seb.py").read_text(encoding="utf-8")

#: The client's own unit test — ``SafeExamBrowser.Configuration.UnitTests/
#: Cryptography/HashAlgorithmTests.cs``, ``MustGeneratePasswordHashCorrectly``.
#: Quoted rather than paraphrased: a vector invented here would agree with this
#: module by construction and prove nothing.
SEB_VECTOR_INPUT = "blabbedyblubbedy"
SEB_VECTOR_OUTPUT = "4adfa806cb610693a6200e4cdbdafeaf352876a35f964a781d691457df9cd378"


@pytest.fixture()
def app_ctx(app):
    """The stored ciphertext is derived from `SECRET_KEY`, so those tests need an app."""
    with app.app_context():
        yield app


# ── 1. the algorithm, against the client's own code ─────────────────────────

def test_the_digest_is_sebs_own_vector_byte_for_byte():
    """Not "a SHA-256": *the* value SEB's own unit test pins.

    This is what rules out the first hypothesis about the reported bug. If the
    algorithm had been wrong — a salt, a trim, UTF-16 instead of UTF-8, Base64
    instead of Base16 — this assertion is where it would show, and it is the one
    assertion that would have been silently satisfied by any self-consistent
    reimplementation.
    """
    assert seb_crypto.sha256_hex(SEB_VECTOR_INPUT) == SEB_VECTOR_OUTPUT


def test_the_stored_shape_is_lowercase_base16_and_nothing_else():
    """The shape SEB parses, and the shape a third-party write-up gets wrong."""
    digest = seb_crypto.sha256_hex("ABCD")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest.islower()
    assert "SHA256" not in digest and ":" not in digest


def _seb_accepts(typed: str, stored: str) -> bool:
    """``ClientResponsibility.IsValidQuitPassword``, transcribed from its source.

    Two lines, because modelling the client's rule is the point: the comparison is
    ``expected.Equals(actual, StringComparison.OrdinalIgnoreCase)``, so the digest's
    *case* can never be why a supervisor is refused. A test that only checked our
    own lowercase output could not tell that from a real lockout.
    """
    return str(stored or "").casefold() == seb_crypto.sha256_hex(typed).casefold()


def test_a_differently_cased_digest_in_the_file_still_matches():
    """SEB ignores case, so an upper-cased digest is a *cosmetic* difference.

    Written down because it decides what a future edit may safely change: the
    digest's case is not load-bearing, and a fix that "normalises" it is not a fix.
    """
    assert _seb_accepts("ABCD", seb_crypto.sha256_hex("ABCD").upper())
    assert _seb_accepts("ABCD", seb_crypto.sha256_hex("ABCD"))
    assert not _seb_accepts("ABCE", seb_crypto.sha256_hex("ABCD"))


# ── 2. the panel: one cell per password, and each holds its own ─────────────

def _alpine_scope(text: str) -> str:
    """The reveal component's own `x-data` source.

    Sliced rather than pattern-matched to the first `}"`, because the component
    contains nested objects (`{ method: 'POST', … }`) and a naive terminator stops
    inside them. The boundary is the next attribute on the same element, and the
    assertion below refuses an empty slice so a rewritten page fails loudly instead
    of passing on nothing.
    """
    start = text.index('x-data="{')
    scope = text[start:text.index('class="max-w-5xl', start)]
    assert "reveal(" in scope, "the slice missed the reveal component"
    return scope


def test_the_two_cells_can_never_show_each_others_password():
    """The defect behind the report: one shared slot, two cells, two labels.

    Before this fix the scope held `password: ''` and each cell rendered
    `x-text="password"`, so revealing the admin password *overwrote the text of the
    quit cell* — the value under the label "Quit password (lets a pupil stop)" could
    be the admin password, and SEB answers wrong-quit-password to it. A supervisor
    has no way to tell: both are four capitals.
    """
    scope = _alpine_scope(PANEL)
    # The shared slot, gone — and gone by *name*, not by "no such string": a bare
    # `this.password = …` inside the component is the mutation the harness injects.
    assert not re.search(r"this\.password(?!s)", scope), \
        "a single shared password slot is back; one cell can overwrite the other"
    assert 'x-text="password"' not in PANEL, \
        "a cell is bound to the shared slot instead of its own kind"
    # One slot per kind, both written and cleared per kind.
    assert 'x-text="passwords[\'{{ kind }}\']"' in PANEL, \
        "the cell is not bound to the kind it is labelled with"
    assert "this.passwords[kind] = data.password" in scope
    assert "this.passwords[kind] = ''" in scope, \
        "hiding one kind must clear only that kind"


def test_the_reveal_button_names_the_kind_it_reveals():
    """A third way to read the wrong secret: a button that asks for the other kind."""
    assert "reveal('{{ kind }}')" in PANEL
    assert "fd.append('kind', kind)" in PANEL
    # And the route reads the same field the page posts.
    assert 'request.form.get("kind")' in ROUTE


# ── 3. the joint statement: what is read aloud is what the file accepts ─────

class _Query:
    """Enough of postgrest for `issue()` — the upsert, and the update after it."""

    def __init__(self, store, table):
        self._store, self._table = store, table
        self._payload = None
        self._on_conflict = None

    def upsert(self, payload, on_conflict=None):
        self._payload, self._on_conflict = payload, on_conflict
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def eq(self, *a, **k):
        return self

    def select(self, *a, **k):
        return self

    def execute(self):
        if self._table == "exam_seb_credential" and "exam_id" in self._payload:
            self._store.row = dict(self._payload)
        return SimpleNamespace(data=[dict(self._payload, id="row-1")])


class _Sb:
    def __init__(self):
        self.row = None

    def table(self, name):
        return _Query(self, name)


def _exam():
    return {"id": "ex-1", "school_id": "sc-1", "teacher_id": "u-1",
            "require_seb": True, "block_right_click": True, "block_screenshot": False}


def test_the_password_read_aloud_is_the_one_the_generated_file_accepts(app_ctx):
    """The end-to-end contract, over the real services, both kinds.

    Built the way the download builds it: the file's two hashes come from the
    credential row, and the plaintext comes from `reveal` on that same row — which
    is exactly the pair of reads that must agree, and did not agree in production
    once a re-issue had replaced the row under an already-distributed file.
    """
    sb = _Sb()
    issued = seb_service.issue(sb, _exam(), start_url="https://s.test/student/exams/ex-1",
                              actor_id="u-1")
    assert issued["ok"] is True
    credential = sb.row

    settings = seb_service.settings_for(
        _exam(), start_url="https://s.test/student/exams/ex-1",
        quit_hash=credential["quit_password_hash"],
        admin_hash=credential["admin_password_hash"])
    from_file = seb_service.decode_seb(seb_service.seb_file_bytes(settings))

    for kind, column in (("quit", "hashedQuitPassword"), ("admin", "hashedAdminPassword")):
        disclosed = seb_service.reveal(credential, kind)
        assert _seb_accepts(disclosed, from_file[column]), (
            f"the {kind} password the panel discloses is not the one the .seb file "
            "accepts — this is the lockout a pupil cannot escape")
        # The shape the operator asked for, at the value the supervisor reads.
        assert len(disclosed) == seb_crypto.PASSWORD_LENGTH == 4
        assert disclosed.isupper() and disclosed.isalpha()

    # And the two secrets are different, so reading one out cannot be mistaken for
    # the other even when both are on screen.
    assert credential["quit_password_hash"] != credential["admin_password_hash"]


# ── 4. the file a re-issue has voided ───────────────────────────────────────

def _credential(updated_at):
    return {"exam_id": "ex-1", "updated_at": updated_at,
            "quit_password_hash": "a" * 64, "admin_password_hash": "b" * 64}


def _download(at):
    return {"created_at": at, "access_type": seb_service.DOWNLOADED}


def _reveal(at):
    return {"created_at": at, "access_type": "view_password"}


def test_a_file_handed_out_before_a_re_issue_is_reported_as_void():
    """The lasting state: the key on screen cannot open the file in a pupil's hands."""
    assert seb_service.file_void_after_reissue(
        _credential("2026-10-14T23:35:12+00:00"),
        [_download("2026-10-14T23:04:32+00:00"), _reveal("2026-10-14T23:35:17+00:00")]) is True


def test_a_file_handed_out_after_the_last_issue_is_not_void():
    assert seb_service.file_void_after_reissue(
        _credential("2026-10-14T23:35:12+00:00"),
        [_download("2026-10-14T23:36:26+00:00")]) is False


def test_reading_a_password_is_not_handing_out_a_file():
    """`view_password` is a different fact: nobody holds a void file because of it."""
    assert seb_service.file_void_after_reissue(
        _credential("2026-10-14T23:35:12+00:00"),
        [_reveal("2026-10-14T22:56:15+00:00"), _reveal("2026-10-14T23:39:56+00:00")]) is False


def test_the_void_state_is_never_claimed_out_of_evidence_that_cannot_be_read():
    """A missing timestamp is not a void file, and an empty log is not a handout.

    The direction matters: this warning tells a teacher to re-issue and reprint, so
    it must not fire on a page whose evidence did not arrive.
    """
    for credential, log in (
        (None, [_download("2026-10-14T23:04:32+00:00")]),          # no credential at all
        (_credential(None), [_download("2026-10-14T23:04:32+00:00")]),   # no issue moment
        (_credential("not-a-date"), [_download("2026-10-14T23:04:32+00:00")]),
        (_credential("2026-10-14T23:35:12+00:00"), []),            # nothing handed out
        (_credential("2026-10-14T23:35:12+00:00"), [{"access_type": "download_file"}]),
        (_credential("2026-10-14T23:35:12+00:00"), None),
    ):
        assert seb_service.file_void_after_reissue(credential, log) is False, (credential, log)


def test_the_panel_says_it_where_a_supervisor_reads_a_password():
    """Above the reveal buttons, not below them, and as a pair the toggle reaches.

    The reader's next act is to read a key aloud; a sentence underneath the thing it
    warns about is a sentence read afterwards. The pair matters for the same reason
    the rest of this page is bilingual — and it is what keeps the page on
    `TRANSLATED`, since a new literal here would be an untranslated paragraph.
    """
    assert 'data-seb-file-void' in PANEL
    assert "{% if file_void %}" in PANEL
    marker = PANEL.index("file_void")
    assert marker < PANEL.index("reveal('{{ kind }}')"), \
        "the warning is below the buttons it is supposed to stop"
    warning = PANEL[marker:PANEL.index("{% endfor %}", marker)]
    assert "x-text=\"t('" in warning, "the warning copy is not a translation pair"
    assert "Kata sandi ini dibuat ulang SETELAH" in warning
    assert "regenerated AFTER" in warning


def test_the_route_and_the_panel_name_the_same_flag():
    """Jinja's `Undefined` is falsy, so a dropped kwarg hides the warning *silently*.

    That is the failure shape this asserts against: the template would render, the
    page would look right, and the one sentence that stops a teacher reading a dead
    key aloud would simply not be there.
    """
    assert "file_void=" in ROUTE
    assert "seb_service.file_void_after_reissue" in ROUTE
    # Gated on the same `live` the rest of the credential surface uses, so a
    # credential row left behind by a disable cannot raise it.
    assert "live and seb_service.file_void_after_reissue" in ROUTE
