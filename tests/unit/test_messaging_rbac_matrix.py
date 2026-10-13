"""The messaging matrix, role by role — contacts, chat, edit, delete, read state.

`AGENTS.md`'s own *Next Steps* still says **verify all 4 RBAC** for the messaging CRUD
and names the operations: contacts list, direct chat, edit, unsend, sembunyikan, hapus,
badge read state. None of them had a test. The endpoints were written for four roles,
the two officials were added to the table in a later release, and nothing pinned what
any of the six may do. Reading them to write this found the two defects it closes:

* the **badge endpoint was the one conversation route that never asked who was
  calling** — ``GET /api/percakapan/<id>/unread-count`` answered any signed-in account
  for any conversation uuid, so the unread count of two other people's thread was
  readable by naming it;
* the **two badges disagreed about deleted messages**: the conversation list excluded
  them and the contact list did not, so ``hapus`` on an unread message left a red dot
  that could never clear while the conversation showed nothing left to read.

``unsend`` and ``sembunyikan`` are answered honestly rather than invented: a sender's
delete *is* the unsend (one ``pesan.is_deleted`` column, so it is delete-for-everyone
and there is no per-user hide — migration 021's ``message_hides`` belongs to the
retired ``notifications`` table, not to ``pesan``). What is pinned here, per role:

* the contact list offers exactly the **mutual** pairs, and never another school;
* a direct chat is created for a mutual pair and refused for every other pair, for
  yourself, and across schools;
* send / read / archive are refused (403) for someone not in the conversation, and a
  conversation that does not exist is 404;
* **edit** is the sender's own act and nobody else's — not even super_admin's;
* **delete** is the sender's, or super_admin's;
* the **badge** counts what is still to read, and the three surfaces that draw one
  agree;
* the page calls routes that exist, offers edit only on the sender's own live
  message, and takes its badge from the contact list.

``AGENTS.md``'s own matrix is printed from the same table: ``tests/unit/
test_role_matrix_docs.py`` holds that half.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "app" / "routes" / "api.py"
COMMS = ROOT / "app" / "templates" / "shared" / "comms.html"

SCHOOL = "sc-a"
OTHER_SCHOOL = "sc-b"
CSRF = "test-csrf-token"

CONV = "11111111-1111-1111-1111-111111111111"
PESAN_GURU = "22222222-2222-2222-2222-222222222222"
PESAN_PRINCIPAL = "33333333-3333-3333-3333-333333333333"
MISSING = "99999999-9999-9999-9999-999999999999"

#: The matrix, written out. ``None`` is where a reader can see the whole answer at
#: once instead of deriving it from the table under test — and `test_the_written_matrix
#: _is_the_servers` is what keeps the two from drifting apart.
EXPECTED_CONTACT_ROLES = {
    "super_admin": {"admin_sekolah", "principal", "vice_principal", "super_admin"},
    "admin_sekolah": {"super_admin", "guru", "murid", "principal", "vice_principal"},
    "principal": {"super_admin", "admin_sekolah", "guru", "principal", "vice_principal"},
    "vice_principal": {"super_admin", "admin_sekolah", "guru", "principal", "vice_principal"},
    "guru": {"admin_sekolah", "guru", "murid", "principal", "vice_principal"},
    "murid": {"guru", "admin_sekolah"},
}

#: The one who is *not* in the conversation the endpoint tests use.
OUTSIDER = "murid-1"


def _uid(role: str) -> str:
    return f"{role}-1"


# ── a database that really filters ───────────────────────────────────────────
#
# Local to this file on purpose: it has to answer `or_` (the conversation list) and
# `rpc` (marking a conversation read), and the shared membership fake deliberately
# answers neither. A fake that returned every row whatever the filter would "prove"
# the school scoping by not testing it.

class Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = []
        self.mode = "select"
        self.payload = None
        self.count_requested = False
        self._single = False
        self._limit = None
        self._offset = 0
        self._order = None
        self._order_desc = False

    # the read chain ─────────────────────────────────────────────────────────
    def select(self, columns="*", count=None, **kw):
        self.count_requested = count is not None
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, {str(v) for v in values}))
        return self

    def ilike(self, column, pattern):
        self.filters.append(("ilike", column, pattern))
        return self

    def or_(self, expression):
        clauses = []
        for part in expression.split(","):
            column, _, rest = part.partition(".")
            op, _, value = rest.partition(".")
            clauses.append((column, op, value))
        self.filters.append(("or", clauses, None))
        return self

    def order(self, column, desc=False):
        self._order, self._order_desc = column, desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def single(self):
        self._single = True
        return self

    maybe_single = single

    # the write chain ────────────────────────────────────────────────────────
    def update(self, payload):
        self.mode, self.payload = "update", dict(payload)
        return self

    def insert(self, payload):
        self.mode, self.payload = "insert", dict(payload)
        return self

    def delete(self):
        self.mode = "delete"
        return self

    # evaluation ─────────────────────────────────────────────────────────────
    @staticmethod
    def _clause(row, column, op, value):
        if op == "eq":
            return str(row.get(column)) == str(value)
        if op == "neq":
            return str(row.get(column)) != str(value)
        raise AssertionError(f"this fake answers eq/neq inside or_(), not {op!r}")

    def _matches(self, row) -> bool:
        for kind, column, value in self.filters:
            if kind == "or":
                if not any(self._clause(row, c, op, v) for c, op, v in column):
                    return False
                continue
            if kind == "eq" and str(row.get(column)) != str(value):
                return False
            if kind == "neq" and str(row.get(column)) == str(value):
                return False
            if kind == "in" and str(row.get(column)) not in value:
                return False
            if kind == "ilike":
                needle = str(value).strip("%").lower()
                if needle not in str(row.get(column) or "").lower():
                    return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.mode == "select":
            found = [r for r in rows if self._matches(r)]
            if self._order:
                found.sort(key=lambda r: str(r.get(self._order) or ""),
                           reverse=self._order_desc)
            if self._offset:
                found = found[self._offset:]
            if self._limit is not None:
                found = found[:self._limit]
            if self._single:
                return Result(dict(found[0]) if found else None)
            return Result([dict(r) for r in found],
                          count=(len(found) if self.count_requested else None))
        if self.mode == "update":
            changed = []
            for row in rows:
                if self._matches(row):
                    row.update(self.payload)
                    changed.append(dict(row))
            if changed:
                self.db.writes.append(("update", self.table, dict(self.payload)))
            return Result(changed)
        if self.mode == "insert":
            new = dict(self.payload)
            new.setdefault("id", f"{self.table}-{len(rows) + 1}")
            new.setdefault("is_read", False)
            new.setdefault("is_deleted", False)
            rows.append(new)
            self.db.writes.append(("insert", self.table, dict(self.payload)))
            return Result([dict(new)])
        kept = [r for r in rows if not self._matches(r)]
        if len(kept) != len(rows):
            self.db.writes.append(("delete", self.table, {}))
        self.db.tables[self.table] = kept
        return Result([])


class Db:
    def __init__(self, **tables):
        self.tables = {name: list(rows) for name, rows in tables.items()}
        self.writes: list[tuple] = []

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        """The one function the chat calls: opening a thread marks it read."""
        if name != "mark_pesan_read":
            raise AssertionError(f"unexpected rpc {name!r}")
        for row in self.tables.setdefault("pesan", []):
            if (str(row.get("percakapan_id")) == str(params["percakapan_id"])
                    and str(row.get("sender_id")) != str(params["user_id"])
                    and not row.get("is_read")):
                row["is_read"] = True
        return Result([])

    def add(self, table, **row):
        self.tables.setdefault(table, []).append(row)
        return row


def _profiles() -> list[dict]:
    """One account per role, plus the two groups that only show up as a second row.

    `guru` and `super_admin` address their **own** role — the group, not the account —
    so a fixture with a single teacher can only ever answer "a teacher sees no
    teacher", which is the fixture and not the rule. The outsider is a school admin
    because that is a role the platform operator's own list *does* contain: a
    cross-school check that used a role the caller cannot see anyway would pass with
    the school filter removed.
    """
    from app.utils.auth import DASHBOARD_FOR_ROLE
    rows = [{"id": _uid(r), "full_name": f"{r} satu", "role": r, "school_id": SCHOOL}
            for r in sorted(DASHBOARD_FOR_ROLE)]
    rows += [{"id": f"{role}-2", "full_name": f"{role} satu", "role": role,
              "school_id": SCHOOL}
             for role in ("guru", "super_admin", "principal", "vice_principal")]
    rows.append({"id": "outsider-1", "full_name": "admin luar",
                 "role": "admin_sekolah", "school_id": OTHER_SCHOOL})
    return rows


#: guru-1 and principal-1 hold a thread; murid-1 is not in it.
def _conversation(**extra) -> dict:
    return dict({"id": CONV, "user_a_id": _uid("guru"), "user_b_id": _uid("principal"),
                 "is_archived": False, "subject": "",
                 "last_message_at": "2026-10-01T00:00:00+00:00"}, **extra)


def _pesan(**extra) -> dict:
    return dict({"id": PESAN_PRINCIPAL, "percakapan_id": CONV,
                 "sender_id": _uid("principal"), "content": "halo",
                 "is_read": False, "is_deleted": False, "is_edited": False,
                 "created_at": "2026-10-01T00:00:00+00:00"}, **extra)


@pytest.fixture()
def db():
    """A school with one account per role, one outsider, and one live thread."""
    return Db(profiles=_profiles(), percakapan=[_conversation()],
              pesan=[_pesan(id=PESAN_PRINCIPAL, sender_id=_uid("principal")),
                     _pesan(id=PESAN_GURU, sender_id=_uid("guru"), content="hai")])


def _client(app, monkeypatch, db, role, uid=None):
    """A signed-in client for `role`, talking to the fake database."""
    from app.routes import api
    from app.services import school_membership
    from app.utils import auth as authmod

    monkeypatch.setattr(api, "get_supabase", lambda: db)
    monkeypatch.setattr(authmod, "_session_for", lambda token: {
        "user_id": uid or _uid(role), "email": "whoever@x", "name": "Whoever",
        "role": role, "school_id": SCHOOL, "status": "active",
    })
    # The school a request is *for* is a membership read for the three roles that may
    # hold more than one. The fixture answers with the home school, so nothing here
    # depends on a database that is not in the test.
    monkeypatch.setattr(school_membership, "resolve_for_request",
                        lambda token, user_id, school, role_: school, raising=False)

    client = app.test_client()
    client.set_cookie("access_token", "tok")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = CSRF
    client.environ_base["HTTP_X_CSRF_TOKEN"] = CSRF
    return client


def _roles() -> list[str]:
    from app.utils.auth import DASHBOARD_FOR_ROLE
    return sorted(DASHBOARD_FOR_ROLE)


# ══════════════════════════════════════════════════════════════════════════════
# The matrix itself
# ══════════════════════════════════════════════════════════════════════════════

class TestTheWrittenMatrixIsTheServers:
    def test_the_table_covers_every_role(self):
        missing = [r for r in _roles() if r not in EXPECTED_CONTACT_ROLES]
        assert not missing, (
            f"{missing} exist as roles but have no row here — the gap the officials "
            "spent their whole life in"
        )

    @pytest.mark.parametrize("role", sorted(EXPECTED_CONTACT_ROLES))
    def test_the_literal_table_is_what_the_server_derives(self, role):
        from app.routes.api import _conversation_roles

        assert set(_conversation_roles(role)) == EXPECTED_CONTACT_ROLES[role]

    @pytest.mark.parametrize("role", sorted(EXPECTED_CONTACT_ROLES))
    def test_every_pair_in_the_table_is_mutual(self, role):
        from app.routes.api import _ALLOWED_RECIPIENT_ROLES

        for other in EXPECTED_CONTACT_ROLES[role]:
            assert role in _ALLOWED_RECIPIENT_ROLES[other], (
                f"{role} would be offered {other}, but a {other} opening the same "
                "conversation is refused — a contact that answers 403"
            )

    def test_an_official_never_gets_a_pupil(self):
        from app.routes.api import _ALLOWED_RECIPIENT_ROLES

        for official in ("principal", "vice_principal"):
            assert "murid" not in EXPECTED_CONTACT_ROLES[official]
            # …and the pupil is still *addressable*: announcing and chatting differ.
            assert "murid" in _ALLOWED_RECIPIENT_ROLES[official]


# ══════════════════════════════════════════════════════════════════════════════
# A. the contact list
# ══════════════════════════════════════════════════════════════════════════════

class TestTheContactList:
    @pytest.mark.parametrize("role", sorted(EXPECTED_CONTACT_ROLES))
    def test_it_offers_exactly_the_mutual_pairs(self, app, monkeypatch, db, role):
        client = _client(app, monkeypatch, db, role)
        resp = client.get("/api/percakapan/contacts")
        assert resp.status_code == 200
        contacts = resp.get_json()["contacts"]
        assert {c["role"] for c in contacts} == EXPECTED_CONTACT_ROLES[role]
        assert _uid(role) not in {c["id"] for c in contacts}, "you are your own contact"

    @pytest.mark.parametrize("role", [r for r in sorted(EXPECTED_CONTACT_ROLES)
                                      if r != "super_admin"])
    def test_another_school_is_never_offered(self, app, monkeypatch, db, role):
        client = _client(app, monkeypatch, db, role)
        ids = {c["id"] for c in client.get("/api/percakapan/contacts").get_json()["contacts"]}
        assert "outsider-1" not in ids, (
            f"a {role} was offered a teacher in another school"
        )

    def test_the_platform_operator_is_the_one_who_sees_every_school(self, app, monkeypatch, db):
        client = _client(app, monkeypatch, db, "super_admin")
        ids = {c["id"] for c in client.get("/api/percakapan/contacts").get_json()["contacts"]}
        assert "outsider-1" in ids

    @pytest.mark.parametrize("role", sorted(EXPECTED_CONTACT_ROLES))
    def test_the_search_offers_the_same_pairs_as_the_list(self, app, monkeypatch, db, role):
        client = _client(app, monkeypatch, db, role)
        users = client.get("/api/percakapan/recipients?q=satu").get_json()["users"]
        assert {u["role"] for u in users} == EXPECTED_CONTACT_ROLES[role]


# ══════════════════════════════════════════════════════════════════════════════
# B. opening a direct chat
# ══════════════════════════════════════════════════════════════════════════════

class TestStartingADirectChat:
    @pytest.mark.parametrize("role", sorted(EXPECTED_CONTACT_ROLES))
    def test_every_pair_the_matrix_allows_is_created_and_every_other_is_refused(
            self, app, monkeypatch, db, role):
        client = _client(app, monkeypatch, db, role)
        for other in _roles():
            if other == role:
                continue
            resp = client.post("/api/percakapan", json={"recipient_id": _uid(other)})
            if other in EXPECTED_CONTACT_ROLES[role]:
                assert resp.status_code in (200, 201), (
                    f"{role} could not open a conversation with {other}: "
                    f"HTTP {resp.status_code} {resp.get_data(as_text=True)[:120]}"
                )
            else:
                assert resp.status_code == 403, (
                    f"{role} opened a conversation with {other}, which the matrix "
                    "says is not mutual"
                )

    def test_a_pupil_is_not_openable_by_an_official(self, app, monkeypatch, db):
        client = _client(app, monkeypatch, db, "principal")
        resp = client.post("/api/percakapan", json={"recipient_id": OUTSIDER})
        assert resp.status_code == 403

    def test_another_school_is_refused(self, app, monkeypatch, db):
        client = _client(app, monkeypatch, db, "guru")
        resp = client.post("/api/percakapan", json={"recipient_id": "outsider-1"})
        assert resp.status_code == 403

    def test_you_cannot_open_one_with_yourself(self, app, monkeypatch, db):
        client = _client(app, monkeypatch, db, "guru")
        resp = client.post("/api/percakapan", json={"recipient_id": _uid("guru")})
        assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# C. inside one conversation: send, read, edit, delete, archive
# ══════════════════════════════════════════════════════════════════════════════

class TestInsideAConversation:
    def test_a_participant_sends_and_a_stranger_cannot(self, app, monkeypatch, db):
        assert _client(app, monkeypatch, db, "guru").post(
            f"/api/percakapan/{CONV}/pesan", json={"content": "hai"}).status_code == 201
        refused = _client(app, monkeypatch, db, "murid").post(
            f"/api/percakapan/{CONV}/pesan", json={"content": "hai"})
        assert refused.status_code == 403

    def test_a_stranger_cannot_read_the_thread(self, app, monkeypatch, db):
        resp = _client(app, monkeypatch, db, "murid").get(f"/api/percakapan/{CONV}/pesan")
        assert resp.status_code == 403

    def test_an_unknown_conversation_is_404(self, app, monkeypatch, db):
        client = _client(app, monkeypatch, db, "guru")
        assert client.get(f"/api/percakapan/{MISSING}/pesan").status_code == 404
        assert client.post(f"/api/percakapan/{MISSING}/pesan",
                           json={"content": "x"}).status_code == 404

    def test_archiving_is_the_participants_act(self, app, monkeypatch, db):
        assert _client(app, monkeypatch, db, "guru").put(
            f"/api/percakapan/{CONV}", json={"is_archived": True}).status_code == 200
        assert _client(app, monkeypatch, db, "murid").put(
            f"/api/percakapan/{CONV}", json={"is_archived": True}).status_code == 403

    def test_only_the_platform_operator_deletes_a_whole_conversation(self, app, monkeypatch, db):
        assert _client(app, monkeypatch, db, "guru").delete(
            f"/api/percakapan/{CONV}").status_code == 403
        assert _client(app, monkeypatch, db, "super_admin").delete(
            f"/api/percakapan/{CONV}").status_code == 200
        assert db.tables["percakapan"] == []


class TestEditingAMessage:
    def test_the_sender_may_and_nobody_else_may(self, app, monkeypatch, db):
        mine = _client(app, monkeypatch, db, "guru")
        resp = mine.put(f"/api/pesan/{PESAN_GURU}", json={"content": "hai, diubah"})
        assert resp.status_code == 200
        row = [r for r in db.tables["pesan"] if r["id"] == PESAN_GURU][0]
        assert row["content"] == "hai, diubah" and row["is_edited"] is True

        assert _client(app, monkeypatch, db, "principal").put(
            f"/api/pesan/{PESAN_GURU}", json={"content": "bukan punyaku"}).status_code == 403
        assert _client(app, monkeypatch, db, "murid").put(
            f"/api/pesan/{PESAN_GURU}", json={"content": "bukan punyaku"}).status_code == 403

    def test_even_the_platform_operator_may_not_rewrite_a_message(self, app, monkeypatch, db):
        resp = _client(app, monkeypatch, db, "super_admin").put(
            f"/api/pesan/{PESAN_GURU}", json={"content": "diedit operator"})
        assert resp.status_code == 403, (
            "edit is the author's act under their own name; the operator can remove a "
            "message, never silently put words in a teacher's mouth"
        )

    def test_an_empty_edit_is_refused(self, app, monkeypatch, db):
        resp = _client(app, monkeypatch, db, "guru").put(
            f"/api/pesan/{PESAN_GURU}", json={"content": "   "})
        assert resp.status_code == 400


class TestDeletingAMessage:
    def test_the_sender_may_and_a_stranger_may_not(self, app, monkeypatch, db):
        assert _client(app, monkeypatch, db, "principal").delete(
            f"/api/pesan/{PESAN_GURU}").status_code == 403
        assert _client(app, monkeypatch, db, "guru").delete(
            f"/api/pesan/{PESAN_GURU}").status_code == 200
        row = [r for r in db.tables["pesan"] if r["id"] == PESAN_GURU][0]
        assert row["is_deleted"] is True

    def test_the_platform_operator_may_remove_any_message(self, app, monkeypatch, db):
        assert _client(app, monkeypatch, db, "super_admin").delete(
            f"/api/pesan/{PESAN_GURU}").status_code == 200

    def test_unsend_is_the_deletion_there_is(self):
        """There is one delete, and it is the sender's own message.

        A per-user *hide* (`sembunyikan`) would need a row per reader; `pesan` has one
        `is_deleted` column, which is delete-for-everyone. Migration 021's
        `message_hides` is bound to the retired `notifications` table, so claiming a
        hide here would be claiming an endpoint and a column that do not exist.
        """
        source = API.read_text(encoding="utf-8")
        assert 'message_hides' not in source
        assert 'is_hidden' not in source


# ══════════════════════════════════════════════════════════════════════════════
# D. the badge — who may read it, and what it counts
# ══════════════════════════════════════════════════════════════════════════════

class TestTheBadge:
    def test_a_stranger_cannot_read_the_count(self, app, monkeypatch, db):
        resp = _client(app, monkeypatch, db, "murid").get(
            f"/api/percakapan/{CONV}/unread-count")
        assert resp.status_code == 403, (
            "the badge endpoint answered any signed-in account for any uuid, which is "
            "how somebody else's unread mail was readable"
        )

    def test_an_unknown_conversation_is_404(self, app, monkeypatch, db):
        resp = _client(app, monkeypatch, db, "guru").get(
            f"/api/percakapan/{MISSING}/unread-count")
        assert resp.status_code == 404

    def test_a_participant_reads_their_own_count(self, app, monkeypatch, db):
        resp = _client(app, monkeypatch, db, "guru").get(
            f"/api/percakapan/{CONV}/unread-count")
        assert resp.status_code == 200
        assert resp.get_json()["unread_count"] == 1   # the principal's, unread

    def test_a_deleted_unread_message_is_not_unread(self, app, monkeypatch, db):
        db.add("pesan", id="44444444-4444-4444-4444-444444444444",
               percakapan_id=CONV, sender_id=_uid("principal"), content="hapus",
               is_read=False, is_deleted=True, is_edited=False,
               created_at="2026-10-02T00:00:00+00:00")
        client = _client(app, monkeypatch, db, "guru")
        assert client.get(f"/api/percakapan/{CONV}/unread-count").get_json()[
            "unread_count"] == 1
        principal = [c for c in client.get("/api/percakapan/contacts").get_json()[
            "contacts"] if c["id"] == _uid("principal")][0]
        assert principal["unread_count"] == 1, (
            "the contact list counts a deleted message as unread, so the red dot can "
            "never be cleared"
        )
        assert client.get("/api/percakapan").get_json()["percakapan"][0][
            "unread_count"] == 1

    def test_the_three_surfaces_agree_on_a_clean_thread(self, app, monkeypatch, db):
        client = _client(app, monkeypatch, db, "guru")
        client.get(f"/api/percakapan/{CONV}/pesan")   # reads it
        assert client.get(f"/api/percakapan/{CONV}/unread-count").get_json()[
            "unread_count"] == 0
        for contact in client.get("/api/percakapan/contacts").get_json()["contacts"]:
            assert contact["unread_count"] == 0
        for conv in client.get("/api/percakapan").get_json()["percakapan"]:
            assert conv["unread_count"] == 0


class TestTheContactOrder:
    """The thread you are in belongs at the top of the list, not under the new names."""

    def test_the_newest_thread_is_first_and_the_never_messaged_follow(
            self, app, monkeypatch, db):
        db.add("percakapan", id="77777777-7777-7777-7777-777777777777",
               user_a_id=_uid("guru"), user_b_id="guru-2", is_archived=False,
               subject="", last_message_at="2026-10-05T00:00:00+00:00")
        contacts = _client(app, monkeypatch, db, "guru").get(
            "/api/percakapan/contacts").get_json()["contacts"]
        with_thread = [c["id"] for c in contacts if c["conversation_id"]]
        without = [c["id"] for c in contacts if not c["conversation_id"]]
        assert [c["id"] for c in contacts] == with_thread + without, (
            "a contact with no conversation at all was listed above a live thread"
        )
        assert with_thread[0] == "guru-2", "the most recent thread is not first"
        assert _uid("principal") in with_thread

    def test_reading_the_thread_clears_the_badge_everywhere(self, app, monkeypatch, db):
        client = _client(app, monkeypatch, db, "guru")
        assert client.get(f"/api/percakapan/{CONV}/pesan").status_code == 200
        assert client.get(f"/api/percakapan/{CONV}/unread-count").get_json()[
            "unread_count"] == 0
        assert client.get("/api/percakapan/contacts").get_json()["contacts"][0][
            "unread_count"] == 0

    def test_your_own_messages_are_never_your_unread(self, app, monkeypatch, db):
        client = _client(app, monkeypatch, db, "principal")
        assert client.get(f"/api/percakapan/{CONV}/unread-count").get_json()[
            "unread_count"] == 1   # the guru's, and only it


class TestTheRulesAreWrittenOnce:
    """A second copy is how the two badges drifted apart in the first place."""

    def test_the_unread_query_exists_once(self):
        source = API.read_text(encoding="utf-8")
        assert source.count('table("pesan").select("id", count="exact")') == 1, (
            "every badge must come from `_unread_count`; a second count query is a "
            "second rule (the two badges drifted apart exactly this way)"
        )
        assert source.count("_unread_count(supabase") == 4, (
            "the definition plus the three surfaces that draw a badge: the "
            "conversation list, the contact list and the badge endpoint"
        )

    def test_the_participation_check_exists_once(self):
        source = API.read_text(encoding="utf-8")
        assert source.count('"Bukan peserta percakapan"') == 1
        assert source.count("_conversation_for(") == 5, (
            "one definition and four call sites (list messages, send, archive, badge)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# E. the page every role opens
# ══════════════════════════════════════════════════════════════════════════════

class TestThePageWiresWhatExists:
    def _text(self) -> str:
        return COMMS.read_text(encoding="utf-8")

    def test_every_api_path_the_page_calls_is_a_route(self, app):
        literals = set(re.findall(r"'(/api/[^']*)'", self._text()))
        assert literals, "the page must call the API"
        rules = [r.rule for r in app.url_map.iter_rules()]
        missing = []
        for literal in literals:
            head = literal.split("?")[0].rstrip("/")
            if not any(r.split("<")[0].rstrip("/").startswith(head) for r in rules):
                missing.append(literal)
        assert not missing, (
            f"the page calls {missing}, which no route answers — a fetch that can only "
            "404"
        )

    def test_the_page_offers_edit_on_the_senders_own_live_message(self):
        source = self._text()
        assert "startEditPesan(msg)" in source
        assert "saveEditPesan()" in source
        assert "'/api/pesan/' + id, {method:'PUT'" in source, (
            "the edit method exists on the server; the page is where it has to be used"
        )
        # The pencil must be behind the same condition as the delete beside it.
        pencil = re.search(r'<button[^>]*startEditPesan\(msg\)[^>]*>', source)
        assert pencil, "no edit button"
        assert 'msg.sender_id === userId && !msg.is_deleted' in pencil.group(0)

    def test_the_edit_banner_says_the_page_is_editing(self):
        source = self._text()
        assert "editingPesanId" in source
        assert "t('Mengedit pesan','Editing message')" in source

    def test_the_badge_is_the_contact_lists_own_number(self):
        source = self._text()
        assert "chatUnreadCount = this.contacts.reduce" in source
        # The only `/unread-count` the page fetches is the announcements'; the chat
        # badge has no endpoint of its own, so there is one arithmetic, not two.
        assert source.count("/unread-count") == 1
        assert "'/api/pengumuman/unread-count'" in source

    def test_the_rendered_badge_says_deleted_not_the_content(self):
        # The server keeps the text of a deleted row (the audit trail is the row), and
        # the page is what must not paint it back.
        source = self._text()
        assert "x-if=\"msg.is_deleted\"" in source
        assert "t('Pesan dihapus','Message deleted')" in source
