"""Forgot password answered "account not found" for accounts that sign in fine.

Measured against production before this file existed, with the checkout's own
``.env`` and read-only calls:

* ``app/utils/auth.get_auth_client()`` returns
  ``create_client(URL, SUPABASE_ANON_KEY)`` — the key a *user* context needs for
  sign-in, ``set_session`` and ``sign_out``. The password-reset routes asked that
  same client for ``admin.list_users()`` / ``admin.get_user_by_id()`` /
  ``admin.update_user_by_id()``, which are GoTrue **admin** calls and need the
  service role: ``AuthApiError: User not allowed``. Every one of them sat inside
  ``except Exception: pass``, so the refusal never reached a log and the visitor
  was told the address was not on file.
* ``admin.list_users()`` is **paged**. With the service key it returns 50 users
  by default; the project has **806**, and all four demo accounts sit at
  positions 779–786 — page 16 of that listing. So even a caller holding the
  right key could not see them, because the address was searched for in page 1.

The two compound: no lookup could ever succeed, so the endpoint was dead for
every account, and its answer blamed the visitor's address.

A third defect sits on the same path and is why a "success" was untrustworthy:
``_send_email`` returned ``None`` and, with no ``SMTP_PASSWORD`` on the box,
logged ``SMTP not configured — email not sent`` and returned — no exception, so
the ``try/except`` around it never fired and the visitor was shown the
"enter your 6-character code" page for a code that was never sent.
"""
from types import SimpleNamespace

import pytest

from tests.conftest import app_instance
from app.routes import auth as mod
from app.utils import auth as auth_utils


# ── fakes: enough of the two Supabase clients to be the two clients ──────────

class _Row:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self._rows = [r for r in self._rows if r.get(column) == value]
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return _Row(self._rows[0]) if self._rows else None


class _Table:
    def __init__(self, rows, recorder=None, refusal=None):
        self._rows = rows
        self._recorder = recorder
        self._refusal = refusal

    def select(self, *_a, **_k):
        return _Query(list(self._rows))

    def update(self, patch):
        return _Write(self._recorder, patch, self._refusal)


class _Write:
    """A profile write, recorded — so a test can say what the flow wrote, not only that
    it wrote. A fake without ``update`` reports a missing write as a broken flow, which
    is why this exists the moment the flow writes one.
    """

    def __init__(self, recorder, patch, refusal=None):
        self._recorder = recorder
        self._patch = patch
        self._refusal = refusal
        self._filters = []

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def execute(self):
        if self._refusal:
            raise RuntimeError(self._refusal)
        self._recorder.append((self._patch, self._filters))
        return _Row([])


class _Admin:
    """The GoTrue admin API, as one key sees it.

    `server_cap` is the point of this fake: the *server* decides how big a page
    is, so asking for 1000 does not promise 1000 back. Modelling that is what
    makes "read the first page and assume it is everything" fail here the way it
    failed in production.
    """

    def __init__(self, users=(), *, refusal=None, default_per_page=50,
                 server_cap=50):
        self._users = list(users)
        self._default_per_page = default_per_page
        self._server_cap = server_cap
        self.refusal = refusal
        self.pages_asked = []
        self.updated = []
        self.by_id_calls = []

    def list_users(self, page=1, per_page=None):
        if self.refusal:
            raise RuntimeError(self.refusal)
        per = min(per_page or self._default_per_page, self._server_cap)
        self.pages_asked.append((page, per))
        start = (page - 1) * per
        return self._users[start:start + per]

    def get_user_by_id(self, user_id):
        if self.refusal:
            raise RuntimeError(self.refusal)
        self.by_id_calls.append(user_id)
        return SimpleNamespace(user=SimpleNamespace(id=user_id, email="by-id@school.id"))

    def update_user_by_id(self, user_id, payload):
        if self.refusal:
            raise RuntimeError(self.refusal)
        self.updated.append((user_id, payload))


class _Client:
    """One object serving both roles, exactly as the app wires it."""

    def __init__(self, admin, *, profiles=None, students=None, teachers=None,
                 update_refusal=None):
        self._admin = admin
        self._tables = {"profiles": profiles or [], "students": students or [],
                        "teachers": teachers or []}
        self._update_refusal = update_refusal
        #: Every profile write the flow made, as ``(patch, filters)``.
        self.updates = []

    @property
    def auth(self):
        return SimpleNamespace(admin=self._admin)

    def table(self, name):
        return _Table(self._tables.get(name, []), self.updates,
                      self._update_refusal)


def _user(email, uid="u-1"):
    return SimpleNamespace(id=uid, email=email)


#: The shape production is in: the account is past the first page of 50.
PAGE_ONE = [_user(f"filler{i}@school.id", f"f{i}") for i in range(50)]
TARGET = _user("admin_smp@scan-grade.app", "target-1")


@pytest.fixture()
def service():
    """The service-role client: `.table(...)` reads and `.auth.admin` writes."""
    return _Admin(PAGE_ONE + [TARGET])


@pytest.fixture()
def app_ctx():
    app = app_instance()
    with app.test_request_context("/auth/forgot-password", method="POST"):
        yield app


def _render_forgot(email, monkeypatch, service_admin, *, sent=True, profiles=None):
    client = _Client(service_admin, profiles=profiles or [])
    monkeypatch.setattr(mod, "get_supabase", lambda: client)
    monkeypatch.setattr(mod, "get_auth_admin", lambda: service_admin)
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: service_admin)
    monkeypatch.setattr(mod, "_send_email", lambda *a, **k: sent)
    app = app_instance()
    with app.test_request_context("/auth/forgot-password", method="POST",
                                  data={"email": email}):
        return mod.forgot_password()


# ── the lookup ───────────────────────────────────────────────────────────────

def test_the_lookup_walks_past_the_first_page(monkeypatch, app_ctx):
    """806 users on this project, and the address searched for is on page 2."""
    admin = _Admin(PAGE_ONE + [TARGET])
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: admin)

    found = auth_utils.find_auth_user_by_email("admin_smp@scan-grade.app")

    assert found is not None and found.id == "target-1"
    assert [p for p, _ in admin.pages_asked] == [1, 2], (
        "the search did not read past its first page, so an account beyond "
        "page 1 is invisible however correct the key is")


def test_the_lookup_does_not_ask_the_anon_client(monkeypatch, app_ctx):
    """The admin API refuses the anon key: `AuthApiError: User not allowed`."""
    refused = _Admin(PAGE_ONE + [TARGET], refusal="User not allowed")
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: refused)
    assert auth_utils.find_auth_user_by_email(TARGET.email) is None


def test_the_admin_helper_reads_the_service_role_client(app_session, monkeypatch):
    """The one line the whole flow depends on, pinned with no route in the way.

    Every other test here patches `get_auth_admin`, so none of them can tell the
    helper's own answer — which is precisely the line that was wrong.
    """
    service, anon = object(), object()
    monkeypatch.setitem(app_session.extensions, "supabase",
                        SimpleNamespace(auth=SimpleNamespace(admin=service)))
    monkeypatch.setitem(app_session.extensions, "supabase_auth",
                        SimpleNamespace(auth=SimpleNamespace(admin=anon)))
    with app_session.test_request_context("/auth/forgot-password"):
        assert auth_utils.get_auth_admin() is service, (
            "the admin interface came from the anon-key client, which the GoTrue "
            "admin API refuses with `User not allowed`")


def test_a_missing_address_stops_at_the_first_empty_page(monkeypatch, app_ctx):
    """A short page is not proof of the end — only an empty one is."""
    admin = _Admin(PAGE_ONE)
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: admin)
    assert auth_utils.find_auth_user_by_email("nobody@school.id") is None
    assert [p for p, _ in admin.pages_asked] == [1, 2], (
        "the walk stopped on a page the server happened to fill, so everything "
        "past it is invisible")


def test_the_search_is_bounded(monkeypatch, app_ctx):
    """A runaway project must not turn one reset request into endless calls."""
    admin = _Admin([_user(f"u{i}@school.id", str(i)) for i in range(500)])
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: admin)
    assert auth_utils.find_auth_user_by_email("nobody@school.id",
                                              per_page=50, max_pages=3) is None
    assert [p for p, _ in admin.pages_asked] == [1, 2, 3], (
        "the walk is unbounded")


def test_an_empty_address_asks_nothing(monkeypatch, app_ctx):
    admin = _Admin(PAGE_ONE + [TARGET])
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: admin)
    assert auth_utils.find_auth_user_by_email("") is None
    assert admin.pages_asked == []


# ── the route, end to end ────────────────────────────────────────────────────

def test_forgot_password_finds_an_account_beyond_the_first_page(monkeypatch):
    """The live symptom: this address answered "tidak ditemukan"."""
    body = _render_forgot("admin_smp@scan-grade.app", monkeypatch,
                          _Admin(PAGE_ONE + [TARGET]))
    assert "verify-reset-code" in body, (
        "the account was not found, so the visitor is sent back to the form")
    assert "tidak ditemukan" not in body


def test_forgot_password_still_reports_an_address_that_is_not_on_file(monkeypatch):
    body = _render_forgot("nobody@school.id", monkeypatch, _Admin(PAGE_ONE + [TARGET]))
    assert "verify-reset-code" not in body
    assert "tidak ditemukan" in body


def test_an_official_is_found_through_the_email_mirror(monkeypatch):
    """A principal has no `students`/`teachers` row, so the mirror is the only
    lookup that can find the two oversight roles — and they are users too.

    The address on the card is all they have; the auth listing is paged and they sit
    deep in it, so before this a principal could only reset by walking the whole
    listing for an account they could not name.
    """
    officer = {"id": "kep-1", "email": "kepsek@scan-grade.app", "phone": "",
               "role": "principal", "full_name": "Kepala Sekolah"}
    body = _render_forgot(
        "kepsek@scan-grade.app", monkeypatch,
        # No auth user carries that address on any page: only the mirror does.
        _Admin([_user("someone.else@scan-grade.app", "x-1")]),
        profiles=[officer],
    )
    assert "verify-reset-code" in body, (
        "a principal could not reset a password: the one address they know lives on "
        "the profile mirror, and nothing read it")


def test_a_visitor_is_not_told_a_code_was_sent_when_it_was_not(monkeypatch):
    """`_send_email` returning None read as success, and the code never arrived."""
    body = _render_forgot("admin_smp@scan-grade.app", monkeypatch,
                          _Admin(PAGE_ONE + [TARGET]), sent=False)
    assert "verify-reset-code" not in body, (
        "the page asks for a code that was never sent")
    assert "Gagal mengirim email" in body


def test_send_email_reports_failure_instead_of_nothing(app_ctx):
    app = app_instance()
    app.config["SMTP_EMAIL"] = "scangrade9@gmail.com"
    app.config["SMTP_PASSWORD"] = ""
    assert mod._send_email("someone@school.id", "subject", "body") is False, (
        "an unconfigured relay must answer False, not None: the caller treats a "
        "truthy answer as 'sent'")


def test_send_email_reports_failure_when_the_relay_refuses(app_ctx, monkeypatch):
    app = app_instance()
    app.config["SMTP_EMAIL"] = "scangrade9@gmail.com"
    app.config["SMTP_PASSWORD"] = "app-password"
    import smtplib as real_smtplib

    def boom(*_a, **_k):
        raise real_smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(real_smtplib, "SMTP_SSL", boom)
    assert mod._send_email("someone@school.id", "subject", "body") is False


# ── the last step of the flow used the same wrong client ─────────────────────

def test_setting_the_new_password_uses_the_service_role_client(monkeypatch):
    """`update_user_by_id` on the anon client is refused, so no password changed.

    The write sits in `set_new_password`, one step after the code is checked —
    and it is the *only* place the flow changes a password, so a refusal here
    leaves the visitor on a page that looks like success and a login that still
    takes the old password.
    """
    admin = _Admin(PAGE_ONE + [TARGET])
    client = _Client(admin)
    monkeypatch.setattr(mod, "get_supabase", lambda: client)
    monkeypatch.setattr(mod, "get_auth_admin", lambda: admin)
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: admin)

    app = app_instance()
    with app.test_request_context(
            "/auth/set-new-password", method="POST",
            data={"email": "admin_smp@scan-grade.app", "password": "new-secret",
                  "confirm_password": "new-secret"}) as ctx:
        ctx.session["reset_email"] = "admin_smp@scan-grade.app"
        mod.set_new_password()

    assert admin.updated, "no password was written: the admin call was refused"
    user_id, payload = admin.updated[0]
    assert payload.get("password"), "the password was written empty"
    assert user_id, "the write did not name the account it belongs to"


# ── a reset is a password change, so it has to be recorded as one ────────────

def _reset(monkeypatch, client, admin, *, email="admin_smp@scan-grade.app"):
    """Drive ``set_new_password`` through a verified code, and hand back the client."""
    monkeypatch.setattr(mod, "get_supabase", lambda: client)
    monkeypatch.setattr(mod, "get_auth_admin", lambda: admin)
    monkeypatch.setattr(auth_utils, "get_auth_admin", lambda: admin)
    app = app_instance()
    with app.test_request_context(
            "/auth/set-new-password", method="POST",
            data={"email": email, "password": "new-secret",
                  "confirm_password": "new-secret"}) as ctx:
        ctx.session["reset_email"] = email
        mod.set_new_password()


def test_a_reset_clears_the_one_time_marker(monkeypatch):
    """A reset by code is a password the *owner* chose, so the account must not still
    be marked as carrying one the school printed.

    Left set, the gate sends the reader to ``/auth/change-password`` the moment they sign
    in with the password they have just chosen — the one-time rule firing on the wrong
    sentence — and ``password_changed_at`` stays NULL, so the school's own record of when
    a password was last replaced is wrong for exactly the accounts that replaced it
    themselves.
    """
    admin = _Admin(PAGE_ONE + [TARGET])
    client = _Client(admin)
    _reset(monkeypatch, client, admin)

    assert client.updates, "the reset recorded nothing about the password it changed"
    patch, filters = client.updates[0]
    assert patch.get("must_change_password") is False, patch
    assert patch.get("password_changed_at"), patch
    assert ("id", "target-1") in filters, filters


def test_a_reset_lands_the_two_officials_on_their_own_dashboard(monkeypatch):
    """The reset page used to keep its own copy of the role URLs — four of the six —
    and a copy is where a new role is forgotten. `principal` and `vice_principal` were
    added after that copy was written.
    """
    from app.utils.auth import DASHBOARD_FOR_ROLE

    for role, home in (("principal", "/principal/dashboard"),
                       ("vice_principal", "/vice-principal/dashboard")):
        admin = _Admin(PAGE_ONE + [TARGET])
        client = _Client(admin, profiles=[{"id": "target-1", "role": role}])
        seen = {}
        monkeypatch.setattr(mod, "render_template",
                            lambda tpl, **kw: seen.update(kw) or tpl)
        _reset(monkeypatch, client, admin)
        assert seen.get("redirect_url") == home, (role, seen)
        assert seen.get("role") == role, (role, seen)
        assert DASHBOARD_FOR_ROLE[role] == home, "the one mapping disagrees with the route"


def test_a_reset_on_a_database_without_the_columns_still_succeeds(monkeypatch):
    """Migration 040 adds both columns. On a box without it the marker write is refused
    by PostgREST *after* the password is already the new one, so the record is skipped
    rather than allowed to turn a completed reset into a failure page — the gate reads
    an absent marker as False, so nothing is left undone either.
    """
    admin = _Admin(PAGE_ONE + [TARGET])
    client = _Client(admin)
    monkeypatch.setattr(auth_utils, "_must_change_unavailable", True)
    _reset(monkeypatch, client, admin)

    assert admin.updated, "the password was not written"
    assert not client.updates, (
        "the reset recorded a marker on a database whose column does not exist")


def test_a_refused_marker_never_reports_the_reset_as_failed(monkeypatch):
    """The password is the reset. Bookkeeping that fails afterwards costs the reader one
    extra change at worst; telling them the reset failed costs them the password they
    just chose — they would open the flow again with a password that no longer works.
    """
    admin = _Admin(PAGE_ONE + [TARGET])
    client = _Client(admin, update_refusal="column does not exist")
    seen = {}
    monkeypatch.setattr(mod, "render_template",
                        lambda tpl, **kw: seen.update({"tpl": tpl, **kw}) or tpl)
    _reset(monkeypatch, client, admin)

    assert admin.updated, "the password was not written"
    assert seen.get("tpl") == "auth/reset_success.html", seen
    assert "error" not in seen, "a completed reset was reported as failed"
