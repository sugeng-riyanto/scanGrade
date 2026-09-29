"""A password a school prints is one-time, and the email on an account is repairable.

Reported: "for admin schools, provide upload and download email and default login to
teachers and students, principals, vice principals each school. Teachers and students are
first time to activate password, then change the password. After that relogin. Activate
forgot password to teachers, students, principals, vice principals each school. Please
check RBAC, api and database."

Measured before this file existed, the three layers disagreed with each other:

* **database** — `profiles` had no way to say an account still carries a password a
  school generated. Every creator writes one and drops it (`_import_students` keeps
  `results["students"] += 1`), the only copy leaves on a printed card, and nothing
  anywhere recorded that it was meant to be replaced. Nor was the account's email
  readable in one query: it lives in Supabase Auth, whose listing is paged at 50.
* **route/RBAC** — login cards were issued for pupils and teachers only
  (`login_cards.KINDS` has two entries), so the two roles added last — principal and
  vice_principal — could be created but never handed a credential. Their page printed
  `o.email` while `PROFILE_FIELDS` selected no such column, so it always showed a dash.
* **api** — nothing could repair the email of an account that already existed, which is
  the address `/auth/forgot-password` sends the reset code to. A school that imported a
  class before it had addresses owned two hundred accounts whose email nobody reads.

What is asserted here, and why each assertion is there:

* the rule a forced change is held to refuses **the defaults this codebase itself
  issues** — `siswa123` is what `student_import` writes for a blank password column, so
  "changing" to it changes nothing on a sheet the whole school has seen;
* the gate lets through exactly the paths a locked-out user needs — the page, both login
  doors, logout, static files, and `/api/` (a school reprints cards mid-sitting, and a
  failed autosave is lost answered work);
* the session reads the new column when the database has it and keeps its identity when
  it does not — the window between a release and its migration has blanked a session
  before;
* issuing a card is what makes a password one-time, so the flag is written there;
* officials can be issued cards at all, scoped by school and by role, with the email as
  the identity — `/auth/login-user` has no NISN or NIP to match for those two roles;
* the email upload **never creates** an account, refuses an address used twice in one
  file, refuses an ambiguous official name, and writes Auth before the mirror.
"""
from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pytest

from tests.conftest import app_instance

ROOT = Path(__file__).resolve().parents[2]
OFFICIALS_TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "officials.html"
ACCOUNTS_TEMPLATE = ROOT / "app" / "templates" / "admin_sekolah" / "accounts.html"
ADMIN_ROUTES = ROOT / "app" / "routes" / "admin_sekolah.py"

from app.services import account_emails as emails  # noqa: E402
from app.services import login_cards  # noqa: E402
from app.services import password_change  # noqa: E402


# ── a supabase stand-in, with the writes it was asked for ────────────────────

class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data or [])


class _Query:
    def __init__(self, fake, table):
        self.fake = fake
        self.table = table
        self._filters: list[tuple[str, object]] = []
        self._update = None

    def select(self, *a, **k):
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def update(self, patch):
        self._update = dict(patch)
        return self

    def execute(self):
        rows = list(self.fake.tables.get(self.table, []))
        for column, value in self._filters:
            if isinstance(value, list):
                rows = [r for r in rows if r.get(column) in value]
            else:
                rows = [r for r in rows if r.get(column) == value]
        if self._update is not None:
            self.fake.writes.append(("update", self.table, self._update, list(self._filters)))
            return _Res(rows)
        return _Res(rows)


class _Admin:
    def __init__(self, fake):
        self.fake = fake

    def update_user_by_id(self, user_id, patch):
        self.fake.auth_writes.append((str(user_id), dict(patch)))
        if self.fake.auth_fails_on and self.fake.auth_fails_on == patch.get("email"):
            raise RuntimeError("Email address already registered")
        return None


class _Fake:
    def __init__(self, tables=None, auth_fails_on=None):
        self.tables = tables or {}
        self.writes: list[tuple] = []
        self.auth_writes: list[tuple] = []
        self.auth_fails_on = auth_fails_on
        self.auth = type("_Auth", (), {"admin": _Admin(self)})()

    def table(self, name):
        return _Query(self, name)


def _rows(*triples) -> list[dict]:
    return [{"row": i, "role": role, "identity": identity, "email": address}
            for i, (role, identity, address) in enumerate(triples, start=2)]


# ── 1. the rule the change is held to ────────────────────────────────────────

class TestTheRuleAForcedChangeIsHeldTo:
    def test_it_accepts_a_password_that_is_actually_different(self):
        assert password_change.change_problem("Kartu-Asli-9", "rahasia-panjang-7",
                                              "rahasia-panjang-7") is None

    def test_it_refuses_the_default_this_codebase_issues(self):
        """`siswa123` is what `student_import` writes for a blank password column.
        'Changing' to it changes nothing on a sheet the whole school has seen."""
        assert password_change.change_problem("Kartu-Asli-9", "siswa123",
                                              "siswa123") == "change_weak"
        assert password_change.change_problem("x", "GURU123", "GURU123") == "change_weak"

    def test_it_refuses_the_password_that_is_already_set(self):
        assert password_change.change_problem("sama-panjang", "sama-panjang",
                                              "sama-panjang") == "change_same"

    def test_it_refuses_a_short_one(self):
        assert password_change.change_problem("Kartu-Asli-9", "pendek",
                                              "pendek") == "change_short"

    def test_it_refuses_a_mismatch_before_it_says_anything_else(self):
        """A typo in the confirmation is the mistake a person can fix fastest, so it is
        reported before the weaker complaints about the value itself."""
        assert password_change.change_problem("Kartu-Asli-9", "panjang-sekali",
                                              "panjang-sekal1") == "password_mismatch"

    def test_it_reports_missing_fields_first(self):
        assert password_change.change_problem("", "", "") == "all_required"

    def test_every_refusal_it_can_return_is_a_message_the_page_can_show(self):
        from app.utils.auth_messages import MESSAGES
        for current, new, confirm in (("a", "b", "c"), ("a", "aa", "aa"),
                                      ("x", "siswa123", "siswa123"), ("", "", "")):
            key = password_change.change_problem(current, new, confirm)
            assert key is None or key in MESSAGES, key


# ── 2. the gate, and what it lets through ────────────────────────────────────

class TestWhatStaysReachableWhileTheChangeIsDue:
    def test_the_page_itself_is_reachable(self):
        from app.utils.auth import CHANGE_PASSWORD_URL, _change_password_exempt
        assert _change_password_exempt(CHANGE_PASSWORD_URL)

    def test_logout_and_both_doors_are_reachable(self):
        from app.utils.auth import _change_password_exempt
        for path in ("/auth/logout", "/auth/login", "/auth/login-user"):
            assert _change_password_exempt(path), path

    def test_static_files_and_api_writes_are_reachable(self):
        """A school reprints cards whenever it likes, including mid-sitting: failing a
        pupil's autosave would lose answered work for a rule about what they *see*."""
        from app.utils.auth import _change_password_exempt
        assert _change_password_exempt("/static/css/tailwind.css")
        assert _change_password_exempt("/api/exams/1/sync-draft")

    def test_every_ordinary_page_is_closed(self):
        from app.utils.auth import _change_password_exempt
        for path in ("/teacher/dashboard", "/student/dashboard", "/principal/dashboard",
                     "/vice-principal/dashboard", "/admin-sekolah/dashboard"):
            assert not _change_password_exempt(path), path

    def test_the_gate_sits_beside_the_pending_gate_in_the_one_decorator(self):
        """Every protected route goes through `login_required`; a gate somewhere else is
        a gate most pages do not have."""
        source = (ROOT / "app" / "utils" / "auth.py").read_text(encoding="utf-8")
        body = source[source.index("def login_required"):]
        body = body[:body.index("\ndef ", 10)]
        assert "must_change_password" in body, (
            "the forced change is not enforced in login_required")
        assert body.index("user_status") < body.index("must_change_password"), (
            "a pending account is sent to activation before the password gate")


# ── 3. the session reads the new column, and survives it being absent ────────

class TestTheSessionKnowsTheFlag:
    def test_a_database_without_the_column_keeps_its_identity(self, monkeypatch):
        """PostgREST refuses a select naming a column it cannot find **in full**, so a
        box that has not run migration 040 yet must fall back rather than lose the
        role, the school and the class along with the new flag."""
        from app.utils import auth as auth_utils

        monkeypatch.setattr(auth_utils, "_preferences_unavailable", False)
        monkeypatch.setattr(auth_utils, "_must_change_unavailable", False)

        seen: list[str] = []

        class _Q:
            def __init__(self, columns):
                self.columns = columns

            def eq(self, *a):
                return self

            def single(self):
                return self

            def execute(self):
                seen.append(self.columns)
                if "must_change_password" in self.columns:
                    raise RuntimeError("column profiles.must_change_password does not exist")
                return type("R", (), {"data": {"role": "murid", "school_id": "s1",
                                               "status": "active", "class_id": "c1",
                                               "preferences": {}}})()

        class _SB:
            def table(self, _name):
                return type("T", (), {"select": lambda _s, cols: _Q(cols)})()

        class _User:
            user = type("U", (), {"id": "u1", "email": "a@b.c",
                                  "user_metadata": {}})()

        monkeypatch.setattr(auth_utils, "get_auth_client",
                            lambda: type("A", (), {"auth": type(
                                "AA", (), {"get_user": staticmethod(lambda _t: _User())})()})())
        monkeypatch.setattr(auth_utils, "get_supabase", lambda: _SB())

        data = auth_utils._fetch_session("token")
        assert data["role"] == "murid", data
        assert data["school_id"] == "s1", data
        assert data["must_change_password"] is False, data
        assert any("preferences" in c and "must_change_password" not in c for c in seen), (
            f"the fallback dropped preferences as well as the new column: {seen}")
        assert auth_utils._must_change_unavailable is True

    def test_the_newest_column_is_the_one_dropped(self):
        """Migrations apply in file order, so the newest column is the one that can be
        missing while every earlier one is present — never the other way round."""
        from app.utils import auth as auth_utils
        assert auth_utils._OPTIONAL_PROFILE_COLUMNS[-1] == "must_change_password"


# ── 4. the sheet that could not be issued for two of the four roles ──────────

class TestOfficialsCanBeHandedACard:
    def _school_rows(self):
        return {"profiles": [
            {"id": "p1", "full_name": "Kepala Satu", "role": "principal",
             "school_id": "s1", "email": "kepsek@school.test"},
            {"id": "v1", "full_name": "Wakil Satu", "role": "vice_principal",
             "school_id": "s1", "email": "wakil@school.test"},
            {"id": "x9", "full_name": "Kepala Sekolah Lain", "role": "principal",
             "school_id": "s2", "email": "other@school.test"},
            {"id": "m1", "full_name": "Murid Satu", "role": "murid",
             "school_id": "s1", "email": "murid@school.test"},
        ]}

    def test_a_card_can_be_collected_for_both_official_roles(self):
        fake = _Fake(self._school_rows())
        found = login_cards.collect(fake, "s1", ["p1", "v1"], "official")
        assert [r["id"] for r in found["rows"]] == ["p1", "v1"], found
        assert found["missing"] == [], found

    def test_the_identity_on_the_card_is_the_email(self):
        """`/auth/login-user` has no NISN or NIP for these roles, so the address typed
        by the school is what they sign in with — and what the card must print."""
        fake = _Fake(self._school_rows())
        card = login_cards.collect(fake, "s1", ["p1"], "official")["rows"][0]
        assert card["identity"] == "kepsek@school.test", card
        assert card["group"] == "Kepala Sekolah", card

    def test_another_schools_official_is_refused_not_skipped(self):
        fake = _Fake(self._school_rows())
        found = login_cards.collect(fake, "s1", ["p1", "x9"], "official")
        assert found["missing"] == ["x9"], found

    def test_a_pupil_cannot_be_turned_into_an_officials_card(self):
        fake = _Fake(self._school_rows())
        found = login_cards.collect(fake, "s1", ["m1"], "official")
        assert found["missing"] == ["m1"], found

    def test_issuing_a_card_is_what_makes_the_password_one_time(self):
        """The password is only one-time because it is written here, printed, and handed
        over — so the flag is set at the same moment, in the one place that writes a
        password to an account."""
        fake = _Fake(self._school_rows())
        rows = login_cards.collect(fake, "s1", ["p1"], "official")["rows"]
        login_cards.set_passwords(fake, rows, password_factory=lambda: "Baru-Sekali-1")
        assert ("update", "profiles", {"must_change_password": True}) in [
            (kind, table, payload) for kind, table, payload, _f in fake.writes], fake.writes
        assert rows[0]["password"] == "Baru-Sekali-1"

    def test_the_route_exists_and_the_page_offers_it(self):
        source = ADMIN_ROUTES.read_text(encoding="utf-8")
        assert "/officials/login-cards" in source, "no route issues officials' cards"
        html = OFFICIALS_TEMPLATE.read_text(encoding="utf-8")
        assert 'action="/admin-sekolah/officials/login-cards"' in html, (
            "the officials page cannot download the sheet")
        assert "selected" in html, "no row can be selected on the officials page"


# ── 5. the email upload: repair only, scoped, planned before written ─────────

class TestUploadingEmails:
    def _fake(self):
        return _Fake({
            "students": [{"id": "st1", "nisn": "111", "school_id": "s1",
                          "profiles": {"id": "st1", "full_name": "Murid Satu",
                                       "school_id": "s1", "email": "lama@school.test"}},
                         {"id": "st2", "nisn": "222", "school_id": "s2",
                          "profiles": {"id": "st2", "full_name": "Murid Dua",
                                       "school_id": "s2", "email": "dua@school.test"}}],
            "teachers": [{"id": "te1", "employee_id": "999", "school_id": "s1",
                          "profiles": {"id": "te1", "full_name": "Guru Satu",
                                       "school_id": "s1", "email": "guru@school.test"}}],
            "profiles": [{"id": "pr1", "full_name": "Kepala Satu", "role": "principal",
                          "school_id": "s1", "email": "lama-kepsek@school.test"},
                         {"id": "pr2", "full_name": "Nama Sama", "role": "principal",
                          "school_id": "s1", "email": "a@school.test"},
                         {"id": "pr3", "full_name": "Nama Sama", "role": "principal",
                          "school_id": "s1", "email": "b@school.test"}],
        })

    def test_a_known_account_is_planned_for_update_with_its_current_address(self):
        fake = self._fake()
        planned = emails.plan(fake, "s1", _rows(("murid", "111", "baru@school.test")))
        assert planned[0]["status"] == "update", planned
        assert planned[0]["current"] == "lama@school.test", planned
        assert planned[0]["user_id"] == "st1", planned

    def test_planning_writes_nothing(self):
        fake = self._fake()
        emails.plan(fake, "s1", _rows(("murid", "111", "baru@school.test")))
        assert fake.writes == [] and fake.auth_writes == [], (fake.writes, fake.auth_writes)

    def test_an_unknown_identifier_is_an_error_and_never_a_creation(self):
        fake = self._fake()
        planned = emails.plan(fake, "s1", _rows(("murid", "404", "baru@school.test")))
        assert planned[0]["status"] == "error", planned
        assert "NISN" in planned[0]["reason"], planned
        emails.apply(planned, fake)
        assert fake.auth_writes == [], "an unknown identifier created an account"

    def test_another_schools_pupil_is_not_found(self):
        fake = self._fake()
        planned = emails.plan(fake, "s1", _rows(("murid", "222", "baru@school.test")))
        assert planned[0]["status"] == "error", planned

    def test_an_address_used_twice_in_one_file_is_refused_whole(self):
        """Two pupils typed into one mailbox means a reset code reaching the wrong
        person, so the second row is refused rather than applied to whoever came first."""
        fake = self._fake()
        planned = emails.plan(fake, "s1", _rows(("murid", "111", "sama@school.test"),
                                                ("guru", "999", "sama@school.test")))
        assert planned[0]["status"] == "update", planned
        assert planned[1]["status"] == "error" and "baris 2" in planned[1]["reason"], planned

    def test_an_ambiguous_official_name_is_refused_rather_than_guessed(self):
        fake = self._fake()
        planned = emails.plan(fake, "s1", _rows(("kepala", "Nama Sama", "baru@school.test")))
        assert planned[0]["status"] == "error", planned
        assert "lebih dari satu" in planned[0]["reason"], planned

    def test_a_deformed_address_is_refused(self):
        fake = self._fake()
        planned = emails.plan(fake, "s1", _rows(("murid", "111", "bukan-email")))
        assert planned[0]["status"] == "error", planned

    def test_an_address_that_is_already_there_is_reported_as_unchanged(self):
        fake = self._fake()
        planned = emails.plan(fake, "s1", _rows(("murid", "111", "lama@school.test")))
        assert planned[0]["status"] == "same", planned
        emails.apply(planned, fake)
        assert fake.auth_writes == [], "an unchanged address was written anyway"

    def test_applying_writes_auth_before_the_mirror(self):
        fake = self._fake()
        planned = emails.plan(fake, "s1", _rows(("murid", "111", "baru@school.test")))
        emails.apply(planned, fake)
        assert fake.auth_writes == [("st1", {"email": "baru@school.test",
                                             "email_confirm": True})], fake.auth_writes
        assert ("update", "profiles", {"email": "baru@school.test"}) in [
            (kind, table, payload) for kind, table, payload, _f in fake.writes], fake.writes

    def test_an_auth_refusal_is_reported_and_the_mirror_is_not_written(self):
        fake = _Fake(self._fake().tables, auth_fails_on="dipakai@school.test")
        planned = emails.plan(fake, "s1", _rows(("murid", "111", "dipakai@school.test")))
        emails.apply(planned, fake)
        assert planned[0]["status"] == "error", planned
        assert "ditolak Auth" in planned[0]["reason"], planned
        assert not [w for w in fake.writes if w[1] == "profiles"], (
            "the mirror was written for an address Auth refused")

    def test_the_template_can_be_read_back_by_the_parser(self):
        """The sheet a school downloads must be a sheet the upload accepts — otherwise
        the example rows come back as errors on the first try."""
        payload = emails.template_bytes("SMP Contoh")
        rows = emails.read_rows(io.BytesIO(payload), "template-email-akun.xlsx")
        assert len(rows) >= 4, rows
        assert {r["role"] for r in rows} == {"murid", "guru", "principal",
                                             "vice_principal"}, rows

    def test_the_summary_counts_read_as_a_sentence(self):
        """Two rows: one account that exists (an update) and one row with no identifier
        (an error). The counts are what the flash sentence is built from, so the shape —
        not only the size — is what is pinned."""
        planned = emails.plan(self._fake(), "s1", _rows(("murid", "111", "baru@school.test"),
                                                        ("murid", "", "c@d.test")))
        counts = emails.summarise(planned)
        assert counts["total"] == 2 and counts["updated"] == 1 and counts["errors"] == 1, counts


# ── 6. every creator marks its account, and the page says so ─────────────────

class TestEveryNewAccountSaysThePasswordIsOneTime:
    def test_the_three_creators_carry_both_fields(self):
        """Three places create an account with an issued password. Forgetting one fails
        silently — the account simply never asks — so the rule lives in one function and
        all three call it."""
        for path in ("app/services/student_import.py", "app/services/teacher_import.py",
                     "app/services/school_officials.py"):
            source = (ROOT / path).read_text(encoding="utf-8")
            assert "account_fields(" in source, f"{path} does not mark its account"

    def test_the_fields_are_the_flag_and_the_email_mirror(self):
        assert password_change.account_fields(" A@B.C ") == {
            "email": "a@b.c", "must_change_password": True}

    def test_the_officials_page_shows_the_marker(self):
        html = OFFICIALS_TEMPLATE.read_text(encoding="utf-8")
        assert "o.must_change_password" in html and 'data-must-change="1"' in html, (
            "the page cannot tell an activated account from one still on its card")

    def test_the_upload_page_states_it_never_creates_an_account(self):
        html = ACCOUNTS_TEMPLATE.read_text(encoding="utf-8")
        assert "tidak ada akun baru" in html, (
            "the page does not say the upload cannot create accounts")


# ── 7. the route: prove the current password, then destroy the session ───────

@contextlib.contextmanager
def _g(user_id="u1", role="murid", email="murid@school.test"):
    from flask import g as flask_g
    with app_instance().test_request_context("/auth/change-password", method="POST"):
        flask_g.user_id = user_id
        flask_g.user_role = role
        flask_g.user_email = email
        yield


def _route(monkeypatch, *, current_ok=True, admin_fails=False):
    from app.routes import auth as auth_routes
    calls = {"sign_in": [], "admin": [], "logged": [], "redirect": None, "flashed": []}

    class _AuthSession:
        def sign_in_with_password(self, payload):
            calls["sign_in"].append(payload)
            if not current_ok:
                raise RuntimeError("invalid credentials")

        def sign_out(self):
            return None

    class _Auth:
        # The route proves the current password through `get_auth_client().auth.…`, so
        # the stand-in has to sit behind the same attribute — otherwise a *correct*
        # password is refused as if Supabase had rejected it, and the test that asks for
        # a successful change passes for the wrong reason.
        auth = _AuthSession()

    class _Admin:
        def update_user_by_id(self, user_id, patch):
            calls["admin"].append((user_id, patch))
            if admin_fails:
                raise RuntimeError("boom")

    class _SB:
        def table(self, _name):
            class _T:
                def update(self, patch):
                    calls["logged"].append(patch)
                    return self

                def eq(self, *a):
                    return self

                def execute(self):
                    return type("R", (), {"data": None})()

            return _T()

        auth = type("A", (), {"admin": _Admin()})()

    monkeypatch.setattr(auth_routes, "get_auth_client", lambda: _Auth())
    monkeypatch.setattr(auth_routes, "get_supabase", lambda: _SB())
    monkeypatch.setattr(auth_routes, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(auth_routes, "invalidate_session", lambda *a, **k: None)
    monkeypatch.setattr(auth_routes, "flash", lambda m, c=None: calls["flashed"].append(m))
    monkeypatch.setattr(auth_routes, "render_template",
                        lambda tpl, **kw: (tpl, kw))
    monkeypatch.setattr(auth_routes, "make_response",
                        lambda resp: type("R", (), {
                            "delete_cookie": lambda self, *a, **k: None})())
    return auth_routes, calls


class TestTheChangePasswordRoute:
    def _post(self, monkeypatch, data, **kw):
        auth_routes, calls = _route(monkeypatch, **kw)
        with _g():
            auth_routes.request.form = data
            out = auth_routes.change_password.__wrapped__()
        return out, calls

    def test_a_wrong_current_password_is_refused_in_words(self, monkeypatch):
        out, calls = self._post(monkeypatch, {"current_password": "salah",
                                             "new_password": "panjang-baru",
                                             "confirm_password": "panjang-baru"},
                                current_ok=False)
        assert calls["admin"] == [], "the password was written despite a wrong current one"
        assert out[0] == "auth/change_password.html" and "error" in out[1], out

    def test_a_refused_rule_never_reaches_supabase(self, monkeypatch):
        out, calls = self._post(monkeypatch, {"current_password": "kartu",
                                              "new_password": "siswa123",
                                              "confirm_password": "siswa123"})
        assert calls["sign_in"] == [] and calls["admin"] == [], calls
        assert out[1]["error"][0].startswith("Password itu"), out

    def test_success_sets_the_password_clears_the_flag_and_ends_the_session(
            self, monkeypatch):
        out, calls = self._post(monkeypatch, {"current_password": "kartu",
                                              "new_password": "panjang-baru",
                                              "confirm_password": "panjang-baru"})
        assert calls["admin"] == [("u1", {"password": "panjang-baru"})], calls
        assert calls["logged"] and calls["logged"][0]["must_change_password"] is False, calls
        assert "password_changed_at" in calls["logged"][0], calls
        assert calls["flashed"], "the reader was not told to sign in again"

    def test_a_write_failure_is_reported_as_a_sentence_not_a_traceback(self, monkeypatch):
        out, calls = self._post(monkeypatch, {"current_password": "kartu",
                                              "new_password": "panjang-baru",
                                              "confirm_password": "panjang-baru"},
                                admin_fails=True)
        assert out[0] == "auth/change_password.html" and "error" in out[1], out
        assert not calls["flashed"], "a failed change flashed a success"

    def test_a_database_without_the_flag_column_still_completes_the_change(
            self, monkeypatch):
        """Reported live: `PGRST204` on `must_change_password` — migration 040 was not
        applied, the password had *already* been written by the admin API, and the
        reader was shown "not saved" for a change that had landed.

        Clearing the flag *records* a change; a database that cannot hold the record
        must not be able to report the change as lost. Nothing is left undone by
        skipping it either, because the session read already reads an absent
        `must_change_password` as False — the pre-040 behaviour, and the honest one.
        """
        from app.utils import auth as auth_utils
        monkeypatch.setattr(auth_utils, "_must_change_unavailable", True)
        out, calls = self._post(monkeypatch, {"current_password": "kartu",
                                              "new_password": "panjang-baru",
                                              "confirm_password": "panjang-baru"})
        assert calls["admin"] == [("u1", {"password": "panjang-baru"})], calls
        assert calls["logged"] == [], (
            "the profile write named a column the database does not have")
        assert not isinstance(out, tuple), (
            "a change that landed was reported as unsaved")
        assert calls["flashed"], "the reader was not told to sign in again"

    def test_the_flag_is_cleared_when_the_column_is_there(self, monkeypatch):
        """The complement, so the guard above cannot be satisfied by never writing."""
        from app.utils import auth as auth_utils
        monkeypatch.setattr(auth_utils, "_must_change_unavailable", False)
        _out, calls = self._post(monkeypatch, {"current_password": "kartu",
                                               "new_password": "panjang-baru",
                                               "confirm_password": "panjang-baru"})
        assert calls["logged"][0]["must_change_password"] is False, calls
