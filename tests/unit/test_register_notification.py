"""What a school gets back the moment it registers, and who it says it is.

Requested: *"Make sure new user get notification to email their registration.
For position: add Teacher, and option other by typing."*

Two defects this file was written against, both invisible on the success page:

* **nobody was told anything.** `/auth/register` created the auth user, the profile
  and the request row and then rendered "Registration Received!" — to a browser. The
  mailbox the school typed into the form received nothing at all, so the only proof
  the registration happened was a page a school could close, and the wait for a
  Super Admin's approval had no acknowledgement on it. Every other user-facing mail
  in this app comes from `app/services/email_bodies.py` (bilingual, escaped, with a
  plain-text half); this one did not exist.
* **the position list decided who the school was.** Four fixed options, and no way to
  name a role that is not on it — a school whose contact is, say, a `Kepala
  Perpustakaan` had to mis-click one of the four, and that wrong string is what the
  Super Admin reads on the approval screen (`requester_position`,
  `requester_name`) and what the profile carries as `full_name`.

The guards below are about the *class* of defect rather than the wording:
an acknowledgement that is really sent (and whose failure cannot undo a
registration that already happened), a position that is what the school typed,
and a form that offers both the common answer and the free one.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routes import auth as routes_auth
from app.services import email_bodies

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "app" / "templates" / "auth" / "register.html"

FORM = {
    "npsn": "12345678",
    "school_name": "SMP Contoh Nusantara",
    "wa": "081234567890",
    "email": "admin@smpcontoh.sch.id",
    "password": "rahasia1",
    "consent": "on",
}


# ── fakes ─────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data if data is not None else []


class _Query:
    """``select(...).eq(...).in_(...).execute()`` and the three write shapes."""

    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.op = "select"
        self.payload = None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, **k):
        self.op, self.payload = "upsert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def execute(self):
        if self.op == "select":
            return _Resp(self.store.rows.get(self.table, []))
        self.store.writes.setdefault(self.table, []).append((self.op, self.payload))
        return _Resp([self.payload])


class _Admin:
    def __init__(self, store):
        self.store = store

    def create_user(self, attributes):
        email = attributes["email"]
        if email in self.store.taken:
            raise Exception("User already registered")
        uid = "u-1"
        self.store.created.append({"id": uid, **attributes})
        return SimpleNamespace(user=SimpleNamespace(id=uid))

    def delete_user(self, uid, should_soft_delete=False):
        self.store.deleted.append(uid)


class _Auth:
    def __init__(self, store):
        self.admin = _Admin(store)


class FakeSupabase:
    def __init__(self, store):
        self.store = store
        self.auth = _Auth(store)

    def table(self, name):
        return _Query(self.store, name)


class _Store:
    def __init__(self):
        self.rows: dict = {}
        self.writes: dict = {}
        self.created: list = []
        self.deleted: list = []
        self.taken: set = set()

    def row_for(self, table):
        for op, payload in self.writes.get(table, []):
            if op in ("insert", "upsert"):
                return payload
        return None


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf(client):
    """The app enforces CSRF on every POST — supply a valid session token."""
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-csrf-token"
    return {"X-CSRF-Token": "test-csrf-token"}


@pytest.fixture
def box(app, monkeypatch):
    """The route with its world faked: one Supabase, no throttle, a mail recorder.

    ``sent`` is the recorder: each entry is what the route handed the sender. The
    route must reach it for the registrant's own address, exactly once.
    """
    store = _Store()
    fake = FakeSupabase(store)
    sent: list = []

    def _record(to_email, subject, body, html=False, text=None, important=False):
        sent.append({"to": to_email, "subject": subject, "body": body, "text": text,
                     "important": important})
        return True

    monkeypatch.setitem(app.extensions, "supabase", fake)
    monkeypatch.setattr(routes_auth, "get_supabase", lambda: fake)
    monkeypatch.setattr(routes_auth, "check_account_limit", lambda scope, account, ip=None: (True, 0))
    monkeypatch.setattr(routes_auth, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(routes_auth, "_send_email", _record)
    return SimpleNamespace(store=store, sent=sent, app=app)


def _post(client, csrf, **extra):
    data = {**FORM, **extra}
    return client.post("/auth/register", data=data, headers=csrf)


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ── 1. the acknowledgement the registrant reads ──────────────────────────────

class TestTheBody:
    def test_it_is_bilingual_and_carries_the_school(self):
        mail = email_bodies.registration_received(
            name="Ibu Sari", school_name="SMP Contoh", npsn="12345678", position="Guru")
        assert "ScanGrade" in mail["subject"]
        assert mail["html"].strip() and mail["text"].strip()
        # An Indonesian half and an English half, each with a phrase only it carries.
        assert "pendaftaran" in mail["html"].lower()
        assert "has been received" in mail["html"].lower()
        assert "SMP Contoh" in mail["text"]
        assert "12345678" in mail["text"]

    def test_a_name_that_looks_like_markup_is_a_name(self):
        mail = email_bodies.registration_received(
            name="<b>Budi</b>", school_name="SMP <script>", npsn="1", position="Guru")
        assert "<b>Budi</b>" not in mail["html"]
        assert "&lt;b&gt;" in mail["html"]
        assert "<script>" not in mail["html"]

    def test_it_says_what_happens_next(self):
        mail = email_bodies.registration_received(
            name="Ibu Sari", school_name="SMP Contoh", npsn="12345678", position="Guru")
        body = (mail["html"] + mail["text"]).lower()
        assert "super admin" in body or "super-admin" in body
        assert "aktivasi" in body or "activation" in body


# ── 2. the route actually sends it ───────────────────────────────────────────

class TestTheRouteSends:
    def test_the_registrant_is_emailed_once(self, client, csrf, box):
        r = _post(client, csrf, position="Guru")
        assert r.status_code == 200
        assert len(box.sent) == 1, "the registration sends exactly one acknowledgement"
        assert box.sent[0]["to"] == FORM["email"]
        assert "SMP Contoh Nusantara" in box.sent[0]["body"]
        assert box.sent[0]["text"], "an HTML-only mail is the shape filters score highest"

    def test_a_relay_that_refuses_does_not_undo_the_registration(self, client, csrf, box, monkeypatch):
        """The account and the request row already exist; an email is a courtesy after."""
        monkeypatch.setattr(routes_auth, "_send_email", lambda *a, **k: False)
        r = _post(client, csrf, position="Guru")
        assert r.status_code == 200
        assert "Pendaftaran Berhasil" in r.get_data(as_text=True)
        assert box.store.created, "the account must survive a failed notification"

    def test_the_acknowledgement_is_not_shouted(self, client, csrf, box):
        """An acknowledgement is not a credential: `important` is reserved for mail
        whose reader cannot wait, and a flag set on everything stops meaning one."""
        _post(client, csrf, position="Guru")
        assert box.sent[0]["important"] is False

    def test_a_sender_that_raises_does_not_500(self, client, csrf, box, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("smtp exploded")
        monkeypatch.setattr(routes_auth, "_send_email", _boom)
        r = _post(client, csrf, position="Guru")
        assert r.status_code == 200
        assert "Pendaftaran Berhasil" in r.get_data(as_text=True)


# ── 3. the position is what the school said it is ───────────────────────────

class TestThePosition:
    def test_a_listed_position_is_stored_as_listed(self, client, csrf, box):
        _post(client, csrf, position="Guru")
        row = box.store.row_for("school_registration_requests")
        assert row is not None and row["requester_position"] == "Guru"

    def test_a_typed_position_is_what_gets_stored(self, client, csrf, box):
        _post(client, csrf, position="other", position_other="Kepala Perpustakaan")
        row = box.store.row_for("school_registration_requests")
        assert row["requester_position"] == "Kepala Perpustakaan", (
            "the sentinel the select submits must never be what an operator reads")
        assert box.store.created, "a typed position registers like any other"

    def test_choosing_other_without_typing_is_refused_before_an_account_exists(self, client, csrf, box):
        r = _post(client, csrf, position="other", position_other="   ")
        assert r.status_code == 200
        assert not box.store.created, "no account may be created for a nameless position"
        assert "Semua field wajib diisi" in r.get_data(as_text=True)

    def test_a_typed_position_is_trimmed_and_bounded(self, client, csrf, box):
        _post(client, csrf, position="other", position_other="  Kepala  " + "x" * 300)
        row = box.store.row_for("school_registration_requests")
        assert row["requester_position"].startswith("Kepala  x")
        assert len(row["requester_position"]) <= 80, (
            "an operator-facing field must not be able to carry a paragraph")


# ── 4. the form offers both the common answer and the free one ──────────────

class TestTheForm:
    def test_teacher_is_an_offered_position(self):
        html = _template()
        assert 'value="Guru"' in html, "a teacher registering their own school cannot say so"
        assert re.search(r'value="Guru"[^>]*x-text="t\(', html), (
            "the new option must be a bilingual pair like every other label on the page")

    def test_other_is_offered_and_reveals_a_text_field(self):
        html = _template()
        assert 'name="position_other"' in html, (
            "the other option has nowhere to type the position into")
        # The field is revealed by the same Alpine state that decides the select's
        # value, so the two can never disagree about whether it is visible.
        assert re.search(r'x-(show|if)="[^"]*position[^"]*other', html), (
            "the typed field is not wired to the select's value")

    def test_the_other_option_is_a_bilingual_pair(self):
        html = _template()
        option = re.search(r'<option value="other"[^>]*>.*?</option>', html, re.S)
        assert option, "no option submits the other sentinel"
        assert "t('" in option.group(0), "the other option reads in one language only"
