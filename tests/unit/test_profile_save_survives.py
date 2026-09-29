"""A school admin must not be told the profile save failed when the write landed.

Reported live: ``https://scangrade.web.id/admin-sekolah/profile`` answers
``Gagal: Server disconnected`` when the form is saved.

That string is not the page's own sentence — it is ``httpx``'s, arriving through the
one line every admin CRUD page ends with::

    except Exception as e:
        flash(f"Gagal: {e}", "error")

What is actually happening is the fault the retrying client exists for. Supabase closes
a keep-alive connection, the client does not notice, the PATCH is written to a dead
socket, and the **reply** is lost — while the row was updated. Re-sending is the one
thing that must not happen (that is how an insert lands twice), so the wrapper settles
a PATCH by reading the rows its own filter names.

These tests drive the real view against a **real** postgrest builder with only its
session scripted, because the confirmation depends on ``path``, ``params`` and ``json``
being the ones a real query carries; a hand-built fake would have to invent exactly the
three things under test. Both directions are pinned, because a recovery that reports
every lost reply as success is worse than the message it replaces:

* a lost reply with the row carrying the patch -> the admin is told it worked, and the
  write is not re-sent;
* a lost reply without the patch -> still reported as a failure;
* a genuine API error (the server *answered*) -> raised at once, with no read.

This file is a guard, not a fix that failed first: the wrapper landed in ``fd51266``,
and production is still answering that URL from a checkout older than it. Its job is to
keep the reported symptom from coming back — the page's own ``Gagal: {e}`` must not be
the way a lost reply reaches an operator.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from flask import g, get_flashed_messages
from supabase import create_client

from app.routes import admin_sekolah as mod
from app.utils.supabase_retry import RetryingClient
from tests.conftest import app_instance

SCHOOL = "aaaaaaaa-0000-0000-0000-00000000000a"
ADMIN = "22222222-0000-0000-0000-00000000000a"
PATH = "/admin-sekolah/profile"

FORM = {
    "name": "SMP Negeri 1 Contoh",
    "npsn": "12345678",
    "address": "Jl. Contoh 1",
    "province": "Jawa Timur",
    "city": "Surabaya",
    "district": "Wonokromo",
    "postal_code": "60243",
    "phone": "031000000",
    "email": "sekolah@scan-grade.app",
    "website": "https://contoh.sch.id",
    "principal_name": "Kepala Contoh",
    "principal_nip": "196501011990031001",
    "tz_offset": "7",
    "email_domain": "contoh.sch.id",
}


def dropped() -> httpx.RemoteProtocolError:
    """The exact exception the live project raises, not a generic one."""
    return httpx.RemoteProtocolError("Server disconnected")


def answered(rows, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=rows,
                          request=httpx.Request("GET", "https://example.supabase.co/rest/v1/x"))


class ScriptedSession:
    """The one place the network would be: every PATCH is dropped, reads are scripted.

    ``rows`` is what the confirming read finds. It defaults to echoing the write's own
    payload, which is the shape of "the statement ran and the reply was lost".
    """

    def __init__(self, *, rows=None, echo: bool = True):
        self._rows = rows
        self._echo = echo
        self.writes: list = []
        self.reads: list = []

    def request(self, method, path, **kw):
        self.writes.append((str(method), path, dict(kw.get("params") or {}), kw.get("json")))
        raise dropped()

    def get(self, path, params=None, headers=None):
        self.reads.append((path, list(params or [])))
        rows = self._rows if self._rows is not None else []
        if self._echo:
            payload = dict(self.writes[-1][3] or {})
            payload["id"] = SCHOOL
            rows = [payload]
        return answered(rows)


def peel(view):
    """The view behind its decorators; the role guard is tested in the suites that own it."""
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def save(monkeypatch, session):
    """Drive ``POST /admin-sekolah/profile`` and answer what the admin saw.

    Returns ``(flashes, response, session)`` — the flashes are read inside the request
    context, which is where ``flash`` puts them.
    """
    client = create_client("https://example.supabase.co", "a.b.c")
    client.postgrest.session = session            # the builder's own session, swapped
    fake = RetryingClient(client)

    monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(mod, "log_activity", lambda *a, **k: None)

    app = app_instance()
    with app.test_request_context(PATH, method="POST", data=FORM):
        g.user_id = ADMIN
        g.user_role = "admin_sekolah"
        g.user_school_id = SCHOOL
        g.user_email = "admin@scan-grade.app"
        g.user_name = "Admin SMP"
        g.tz_offset = 7
        response = peel(mod.profile)()
        flashes = get_flashed_messages(with_categories=True)
    return flashes, response, session


def messages(flashes):
    return [message for _category, message in flashes]


class TestALostReplyIsNotReportedAsAFailedSave:

    def test_the_admin_is_told_the_profile_was_saved(self, monkeypatch):
        flashes, _response, session = save(monkeypatch, ScriptedSession())
        assert messages(flashes) == ["Profil sekolah berhasil diperbarui"], flashes
        assert not any(m.startswith("Gagal") for m in messages(flashes)), (
            "a reply that was lost reached the operator as `Gagal: …` — the reported "
            "symptom, and the reason they press Save again on a row that was written")
        assert len(session.writes) == 1, (
            "the write was re-sent after its reply was lost, which is how a statement "
            "lands twice")

    def test_the_confirming_read_names_this_school_row(self, monkeypatch):
        _flashes, _response, session = save(monkeypatch, ScriptedSession())
        assert session.reads, "nothing asked whether the write landed"
        assert session.reads[0][1] == [("id", f"eq.{SCHOOL}")], (
            f"the read did not ask about this school's row: {session.reads[0][1]}")

    def test_a_write_that_did_not_land_is_still_a_failure(self, monkeypatch):
        """The other direction, and the reason this is a confirmation and not a shrug."""
        session = ScriptedSession(rows=[{"id": SCHOOL, "name": "Nama Lama"}], echo=False)
        flashes, _response, _session = save(monkeypatch, session)
        assert messages(flashes)[0].startswith("Gagal:"), (
            "a profile that was *not* updated was reported as saved")
        assert "Nama Lama" not in " ".join(messages(flashes)), (
            "the page quoted the row back at the operator instead of saying it failed")


class TestAServerAnswerIsNeverSecondGuessed:

    def test_a_real_api_error_is_reported_without_a_read(self, monkeypatch):
        from postgrest.exceptions import APIError

        session = ScriptedSession()
        session.request = lambda method, path, **kw: (_ for _ in ()).throw(
            APIError({"message": "column does not exist"}))
        flashes, _response, _session = save(monkeypatch, session)
        gave_up = messages(flashes)
        assert gave_up and gave_up[0].startswith("Gagal:"), gave_up
        assert session.reads == [], (
            "a server answer was second-guessed with a read of the row")
