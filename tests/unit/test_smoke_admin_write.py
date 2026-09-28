"""The one thing the deploy smoke test writes, held to what it claims.

Everything else in the smoke test is a GET, so a school that has quietly gone
read-only — a database role without INSERT, a route whose write is swallowed into
a flash — passes every check in it and reaches a user. The reply to a POST is not
evidence either: in this app a failed insert flashes its error *and redirects*, so
a POST that stored nothing and a POST that stored a row answer the same `302`. The
only thing that can tell them apart is the page the school admin reads next.

So these tests are about the two ways that proof can be fake:

* the run never looks at the page, and reports success from the POST's own reply;
* the run looks, but never cleans up, and leaves a probe row inside a real school.

The session is faked, not the claim: every GET and POST is scripted, so the
sequence itself — read, create, read, delete, read — is what is asserted, and a run
that skips a read fails here.

One test deliberately does not use a fake page: the row parser is pinned against
the row markup **read out of the template**, so a parser that matches only this
file's idea of a subject card cannot pass.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SMOKE_PY = ROOT / "deploy" / "smoke_test.py"
SUBJECTS_TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "subjects.html"

SUBJECT_ID = "5b1f0b2c-3d4e-4f50-8a91-2b3c4d5e6f70"
STALE_ID = "0a0b0c0d-1e2f-4051-8293-a4b5c6d7e8f9"
REAL_ID = "11111111-1111-4111-8111-111111111111"


def _smoke_module():
    spec = importlib.util.spec_from_file_location("sg_smoke_admin_write", SMOKE_PY)
    module = importlib.util.module_from_spec(spec)
    # Register before executing: @dataclass resolves annotations through
    # sys.modules, and a module that is not registered makes it explode.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _smoke_module()

CSRF = "c0ffee" + "0" * 20


# ── a page, a session, both scripted ─────────────────────────────────────────

def subjects_page(*rows: tuple[str, str], csrf: str = CSRF) -> str:
    """A subjects page carrying the csrf meta tag and one card per row."""
    cards = "".join(
        f'<form method="POST" action="/admin-sekolah/subjects/{sid}/delete">'
        f'<input type="hidden" name="confirm" value="1"></form>'
        f'<p class="font-extrabold text-slate-700 text-center">{name}</p>'
        for sid, name in rows)
    return (f'<html><head><meta name="csrf-token" content="{csrf}"></head>'
            f"<body>{cards}</body></html>")


class Response:
    def __init__(self, status: int = 200, text: str = "", location: str = ""):
        self.status_code = status
        self.text = text
        self.headers = {"Location": location} if location else {}


class FakeSession:
    """Scripted HTTP, in order, and it keeps what it was asked."""

    def __init__(self, gets: list, posts: list | None = None):
        self._gets = list(gets)
        self._posts = list(posts or [])
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict, dict]] = []

    def get(self, url, **_kw):
        self.gets.append(url)
        if not self._gets:
            raise AssertionError(f"unexpected GET {url}: the run asked for a page "
                                 "the test did not script")
        nxt = self._gets.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def post(self, url, data=None, **kw):
        self.posts.append((url, dict(data or {}), dict(kw)))
        if not self._posts:
            raise AssertionError(f"unexpected POST {url}: the run wrote when the "
                                 "test did not script a write")
        nxt = self._posts.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def account() -> "smoke.Account":
    return smoke.Account("admin_sekolah", "kepsek@example.test", "pw")


def run(gets: list, posts: list | None = None):
    """Drive the probe and hand back (result, session)."""
    session = FakeSession(gets, posts)
    res = smoke.Result()
    smoke.check_admin_write(session, "https://box.test", account(), res)
    return res, session


def created_name(session: FakeSession) -> str:
    """The name the run actually posted, so no test hard-codes the pattern."""
    url, data, _ = session.posts[0]
    assert url.endswith(smoke.SUBJECT_CREATE_PATH), url
    return data["name"]


# ── the name, and the parser ─────────────────────────────────────────────────

def test_the_probe_name_names_itself_and_cannot_collide() -> None:
    """It lands in a real school's list and in the audit log, so it has to be
    recognisable as not-a-subject — and two runs must not collide, or the second
    one's duplicate check would refuse the write and look like a broken route."""
    first = smoke.probe_subject_name(datetime(2026, 9, 29, 1, 2, 3, tzinfo=timezone.utc))
    second = smoke.probe_subject_name(datetime(2026, 9, 29, 1, 2, 4, tzinfo=timezone.utc))
    assert first != second
    assert smoke.PROBE_PREFIX in first
    assert "2026-09-29" in first, f"the name does not say when it was made: {first!r}"
    assert smoke.probe_subject_name().startswith(smoke.PROBE_PREFIX)


def test_the_parser_reads_the_row_the_template_really_draws() -> None:
    """Pinned to the template, not to this file's idea of a card.

    A parser tested against its own fixture proves only that two hand-written
    strings agree. The markup below is lifted out of `subjects.html` — the delete
    form and the name paragraph, in that order — and only the Jinja values are
    substituted, so a template change that moves either one fails here.
    """
    template = SUBJECTS_TEMPLATE.read_text(encoding="utf-8")
    form = re.search(
        r'<form method="POST" action="/admin-sekolah/subjects/\{\{ s\.id \}\}/delete"',
        template)
    name = re.search(r"<p class=\"font-extrabold[^\"]*\">\{\{ s\.name \}\}</p>", template)
    assert form and name, (
        "the subjects page no longer draws a delete form and a name paragraph the "
        "way the parser expects — fix the parser, not this assertion")

    drawn = template[form.start():name.end()]
    html = drawn.replace("{{ s.id }}", SUBJECT_ID).replace("{{ s.name }}", "Matematika")
    assert smoke.probe_subject_rows(html) == [(SUBJECT_ID, "Matematika")]

    # Two cards, one page: the id must belong to the name drawn above it, or the
    # probe would delete somebody else's subject.
    two = html + html.replace(SUBJECT_ID, STALE_ID).replace("Matematika", "Fisika")
    assert smoke.probe_subject_rows(two) == [(SUBJECT_ID, "Matematika"),
                                             (STALE_ID, "Fisika")]


def test_a_page_with_no_cards_parses_to_nothing() -> None:
    assert smoke.probe_subject_rows(subjects_page()) == []
    assert smoke.probe_subject_rows("<html>not a subjects page</html>") == []


# ── the write, and the proof it happened ─────────────────────────────────────

def test_a_create_that_never_reaches_the_page_fails_and_names_the_row() -> None:
    """The whole reason this check exists: a read-only school still answers `302`.

    Twice, because one dropped reply must not quarantine a release — so the second
    attempt is scripted to fail too, and *that* is the verdict.
    """
    res, session = run(
        gets=[Response(200, subjects_page()),
              Response(200, subjects_page()),          # nothing new on the page
              Response(200, subjects_page())],         # ... still nothing
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    name = created_name(session)
    assert res.failures, "a school that silently refused the write passed"
    message = " ".join(res.failures)
    assert name in message, (
        "the failure does not name the row, so a leftover cannot be found: " + message)
    assert "read-only" in message or "no row" in message or "not on the page" in message
    assert "2 attempts" in message, (
        "the verdict does not say how hard it was tried: " + message)


def test_one_dropped_reply_does_not_roll_back_a_release() -> None:
    """This app's connection to Supabase drops replies — the route flashes
    `Gagal: Server disconnected` and stores nothing — so the first attempt failing
    is a property of one connection. Two in a row is a property of the release."""
    name = smoke.probe_subject_name()
    res, _ = run(
        gets=[Response(200, subjects_page()),
              Response(200, subjects_page()),                     # not there yet
              Response(200, subjects_page((SUBJECT_ID, name))),   # the retry landed
              Response(200, subjects_page())],                    # and it was removed
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures == [], res.failures
    assert any("retrying" in w for w in res.warnings), (
        "a first attempt that failed was retried without saying so")


def test_a_row_that_survives_its_own_delete_fails_and_names_its_id() -> None:
    name = smoke.probe_subject_name()
    mine = Response(200, subjects_page((SUBJECT_ID, name)))
    res, session = run(
        gets=[Response(200, subjects_page()), mine, mine, mine],   # still there, twice
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures, "a delete that did nothing was accepted"
    assert SUBJECT_ID in " ".join(res.failures), (
        "the failure does not say which row is still in the school")
    assert any(u.endswith(f"/subjects/{SUBJECT_ID}/delete") for u, _, _ in session.posts), (
        "the run did not even try to remove the row it created")


def test_the_happy_path_creates_then_deletes_and_proves_both() -> None:
    name = smoke.probe_subject_name()
    res, session = run(
        gets=[Response(200, subjects_page()),
              Response(200, subjects_page((SUBJECT_ID, name))),
              Response(200, subjects_page())],
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures == [], res.failures
    create_url, create_data, create_kw = session.posts[0]
    assert create_url.endswith(smoke.SUBJECT_CREATE_PATH)
    assert create_data["name"] == name
    delete_url, delete_data, _ = session.posts[1]
    assert delete_url.endswith(f"/subjects/{SUBJECT_ID}/delete")
    assert delete_data.get("confirm") == "1", (
        "a delete that breaks a reference is refused without confirmation")
    # Neither write follows its redirect: the `302` is what proves the route was
    # reached at all, and the rendered page — read separately — is the evidence.
    assert [kw.get("allow_redirects") for _, _, kw in session.posts] == [False, False]
    assert len(session.gets) == 3, (
        "the run must read the page before, after the create and after the delete")


def test_a_failed_write_quotes_the_route_s_own_reason() -> None:
    """Every write route here catches its exception and flashes it as `Gagal: …`,
    so the page carries the cause — and a failure that stops at "it did not take"
    sends the reader to a shell for something the page already said."""
    flash = ('<div class="p-3">Gagal: new row violates row-level security policy '
             'for table "subjects"</div>')
    res, _ = run(
        gets=[Response(200, subjects_page()),
              Response(200, subjects_page() + flash),   # the route's own words
              Response(200, subjects_page() + flash)],
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures
    assert "row-level security policy" in " ".join(res.failures), (
        "the route's own words were dropped: " + " ".join(res.failures))


def test_a_write_lost_to_the_connection_is_reported_without_holding_the_release() -> None:
    """`app/utils/supabase_retry.py` retries reads and deliberately not writes,
    because a dropped connection does not say whether the statement ran — so on a
    lossy link a write keeps failing while reads quietly succeed. This box measures
    that: the delete hit it in 3 of 3 live runs. Holding a release for it would turn
    that accepted asymmetry into "this box may never release"."""
    flash = '<div class="p-3">Gagal: Server disconnected</div>'
    res, _ = run(
        gets=[Response(200, subjects_page()),
              Response(200, subjects_page() + flash),
              Response(200, subjects_page() + flash)],
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures == [], (
        "a lost TCP reply was allowed to roll the release back: " + str(res.failures))
    assert any("Server disconnected" in w for w in res.warnings), (
        "the connection loss was not reported at all: " + repr(res.warnings))
    assert any("link, not the release" in w for w in res.warnings), (
        "the warning does not say why it is not a release problem")


def test_a_create_the_database_refused_holds_the_release() -> None:
    """The case this check exists for, and it must not be mistaken for the link:
    a narrowed database role refuses the insert and says so in its own words."""
    flash = '<div class="p-3">Gagal: permission denied for table subjects</div>'
    res, _ = run(
        gets=[Response(200, subjects_page()),
              Response(200, subjects_page() + flash),
              Response(200, subjects_page() + flash)],
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures, "a school that refused every write passed as a link problem"
    assert "permission denied" in " ".join(res.failures)


def test_a_create_that_never_returns_a_redirect_is_reported() -> None:
    """The route always redirects, so a 403 is the write never being reached —
    the app's CSRF hook, not the school's database."""
    res, session = run(
        gets=[Response(200, subjects_page())],
        posts=[Response(403, "CSRF token invalid")],
    )
    assert res.failures
    assert "403" in " ".join(res.failures)
    assert session.posts[0][0].endswith(smoke.SUBJECT_CREATE_PATH)


def test_both_writes_carry_the_csrf_token_the_page_handed_out() -> None:
    name = smoke.probe_subject_name()
    _, session = run(
        gets=[Response(200, subjects_page(csrf="tok")),
              Response(200, subjects_page((SUBJECT_ID, name), csrf="tok")),
              Response(200, subjects_page(csrf="tok"))],
        posts=[Response(302), Response(302)],
    )
    assert [data.get("_csrf_token") for _, data, _ in session.posts] == ["tok", "tok"], (
        "without the token the app's global CSRF hook answers 403 and the probe "
        "would report the write path as broken")


def test_a_page_that_cannot_be_read_is_a_failure_not_a_silent_skip() -> None:
    """Reporting nothing about the write path is how a read-only school reaches a
    user, so a probe that cannot run its own check must say so."""
    res, session = run(gets=[Response(503, "")])
    assert res.failures
    assert session.posts == [], "it wrote into a school whose page it could not read"

    res, _ = run(gets=[Response(200, "<html>no csrf meta tag</html>")])
    assert res.failures, "a page with no csrf token cannot arm the write"


def test_a_create_that_errors_is_reported_with_the_status() -> None:
    res, session = run(
        gets=[Response(200, subjects_page()), Response(200, subjects_page())],
        posts=[Response(500, "")],
    )
    assert res.failures
    assert "500" in " ".join(res.failures)
    # No id was ever learned, so there is nothing to delete — and the run must say
    # so rather than pretend it cleaned up.
    assert all("delete" not in url for url, _, _ in session.posts)


def test_a_delete_lost_to_the_connection_names_the_row_it_is_leaving() -> None:
    """The drop can happen *after* the row is gone — `Server disconnected` is thrown
    while reading the reply — so the reply is not what decides; the list is. And when
    the row really is left behind, the report has to name it."""
    import requests

    name = smoke.probe_subject_name()
    mine = Response(200, subjects_page((SUBJECT_ID, name)))
    res, _ = run(
        gets=[Response(200, subjects_page()), mine, mine, mine],
        posts=[Response(302, location="/admin-sekolah/subjects"),
               requests.ConnectionError("connection reset"),
               requests.ConnectionError("connection reset")],
    )
    assert res.failures == [], res.failures
    warned = " ".join(res.warnings)
    assert SUBJECT_ID in warned, "the leftover row is not named: " + warned
    assert "next run clears them" in warned, (
        "nothing tells the operator this heals by itself")


def test_a_delete_the_database_refused_holds_the_release() -> None:
    """The other half: the route answered, the row did not move, and the page did not
    blame the connection. That is a write path that does not work."""
    name = smoke.probe_subject_name()
    mine = Response(200, subjects_page((SUBJECT_ID, name)))
    res, _ = run(
        gets=[Response(200, subjects_page()), mine, mine, mine],
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures, "a delete that silently did nothing passed"
    assert SUBJECT_ID in " ".join(res.failures)


def test_a_dropped_delete_reply_is_not_called_a_failed_delete() -> None:
    """The row is gone although the reply never arrived: reading the list settles
    it, and a failure here would roll back a release over a lost packet."""
    import requests

    name = smoke.probe_subject_name()
    res, _ = run(
        gets=[Response(200, subjects_page()),
              Response(200, subjects_page((SUBJECT_ID, name))),
              Response(200, subjects_page())],          # gone, whatever the reply did
        posts=[Response(302, location="/admin-sekolah/subjects"),
               requests.ConnectionError("connection reset")],
    )
    assert res.failures == [], res.failures


def test_a_probe_row_left_by_an_earlier_run_is_removed_and_reported() -> None:
    """A run that died between its two writes leaves a fake subject in a real
    school. The next run owns the prefix, so it clears it — and says so, because a
    row that keeps coming back is a symptom worth reading."""
    stale = smoke.PROBE_PREFIX + "_2026-01-01T00:00:00Z"
    name = smoke.probe_subject_name()
    res, session = run(
        gets=[Response(200, subjects_page((STALE_ID, stale))),
              Response(200, subjects_page((STALE_ID, stale), (SUBJECT_ID, name))),
              Response(200, subjects_page((STALE_ID, stale))),   # its own row is gone
              Response(200, subjects_page())],
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures == [], res.failures
    assert any(url.endswith(f"/subjects/{STALE_ID}/delete") for url, _, _ in session.posts), (
        "a leftover probe row from an earlier run was left in the school")
    assert any(stale in w for w in res.warnings), (
        "the leftover was removed without saying so: " + repr(res.warnings))


def test_it_deletes_nothing_that_is_not_its_own_probe_row() -> None:
    """The prefix is the only licence this check has to delete anything."""
    name = smoke.probe_subject_name()
    res, session = run(
        gets=[Response(200, subjects_page((REAL_ID, "Matematika"))),
              Response(200, subjects_page((REAL_ID, "Matematika"), (SUBJECT_ID, name))),
              Response(200, subjects_page((REAL_ID, "Matematika")))],
        posts=[Response(302, location="/admin-sekolah/subjects"),
               Response(302, location="/admin-sekolah/subjects")],
    )
    assert res.failures == [], res.failures
    assert all(REAL_ID not in url for url, _, _ in session.posts), (
        "the probe touched a subject that is not its own")
