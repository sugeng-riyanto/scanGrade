"""The retry, proved against a hiccup instead of asserted about one.

`tests/unit/test_auth_create_retry.py` pins that the retry *runs*. This file pins
what the retry must not do: turn one teacher into two accounts, or leave the
failed attempt behind as an orphan. The claim in `auth_retry.py` is that repeating
a create is safe **because GoTrue answers the generic database error after its
insert raised and rolled back** — so the proof has to talk to something that
actually models that rollback, and the create call itself has to be the real one.

So the harness is an in-memory auth server with exactly the one property the
argument rests on, and nothing else faked:

* a create that hits the hiccup raises the generic message **with nothing written**
  (GoTrue's rollback, which is why a repeat cannot duplicate);
* a committed create refuses the same email a second time, and refuses a state the
  role tables can reject;
* ``delete_user`` cascades, the way ``auth → profiles → teachers`` does.

``teacher_import.create_teacher_account`` — the live code path, retry, rollback and
one-time-password stamp included — is driven against it. The assertions across the
three stores are the proof: exactly one account, its profile and role row, and
nothing from the failed attempt.
"""
from __future__ import annotations

import pytest

from app.errors import ScanGradeException
from app.utils import auth_health

#: A stand-in for the GoTrue the teacher creator talks to. Only the transaction
#: semantics matter here; the rest of the admin surface is whatever the create
#: path touches.
GENERIC = "Database error creating new user"
ALREADY = "A user with this email address has already been registered"


class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Created:
    def __init__(self, uid):
        self.user = type("User", (), {"id": uid})()


class _Table:
    """A PostgREST table: upsert by id, or a filtered read, or a refusal."""

    def __init__(self, harness, name):
        self.harness = harness
        self.name = name
        self.payload = None
        self.filters = []

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        return self

    def upsert(self, payload):
        self.payload = payload
        return self

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        if self.payload is not None:
            if self.name in self.harness.fail_tables:
                # The write the database is entitled to reject -- and the one that
                # forces the rollback the account's completion depends on.
                raise RuntimeError(f"permission denied for table {self.name}")
            self.harness.tables.setdefault(self.name, {})[self.payload["id"]] = \
                dict(self.payload)
            return _Resp([self.payload])
        rows = [
            row for row in self.harness.tables.get(self.name, {}).values()
            if all(str(row.get(c)) == str(v) for c, v in self.filters)
        ]
        return _Resp(rows[:1])


class _Admin:
    def __init__(self, harness):
        self.harness = harness

    def create_user(self, attributes):
        h = self.harness
        email = attributes["email"]
        h.attempts += 1
        if email in h.by_email:
            # A landed account is what "already registered" means, and it is a fact
            # about the school's data -- never retried.
            raise Exception(ALREADY)
        if h.attempts <= h.hiccups:
            if h.landed_on_hiccup:
                # The *unfaithful* server, kept only so the harness can be shown to
                # tell the two apart: a hiccup that wrote before failing.
                h._commit(email, attributes)
            raise Exception(GENERIC)
        uid = h._commit(email, attributes)
        return _Created(uid)

    def delete_user(self, uid, should_soft_delete=False):
        h = self.harness
        h.deleted.append(uid)
        email = h.users.pop(uid, {}).get("email")
        if email is not None:
            h.by_email.pop(email, None)
        for rows in h.tables.values():
            rows.pop(uid, None)


class _Auth:
    def __init__(self, harness):
        self.admin = _Admin(harness)


class LiveGoTrue:
    """An in-memory GoTrue whose only opinion is what a failed insert leaves."""

    def __init__(self, hiccups=0, landed_on_hiccup=False, fail_tables=()):
        self.attempts = 0        # create_user calls that reached the server
        self.commits = 0         # account rows actually written
        self.deleted = []
        self.users = {}          # uid -> auth row
        self.by_email = {}       # email -> uid, the uniqueness GoTrue enforces
        self.tables = {}         # table -> {id: row}, what delete_user cascades to
        self.hiccups = hiccups
        self.landed_on_hiccup = landed_on_hiccup
        self.fail_tables = set(fail_tables)
        self.auth = _Auth(self)

    def _commit(self, email, attributes):
        uid = f"uid-{self.attempts}"
        self.users[uid] = {"id": uid, **attributes}
        self.by_email[email] = uid
        self.commits += 1
        return uid

    def table(self, name):
        return _Table(self, name)


@pytest.fixture(autouse=True)
def _clean_auth_record(tmp_path, monkeypatch):
    """Keep the cross-worker marker out of the shared temp file."""
    auth_health.reset()
    monkeypatch.setattr(auth_health, "_STATE_FILE_OVERRIDE", tmp_path / "auth.json")


def _create_teacher(harness, **overrides):
    from app.services import teacher_import

    kwargs = dict(
        school_id="s1", full_name="Budi Santoso", email="budi@sekolah.invalid",
        password="Probe1234!", employee_id="12345",
    )
    kwargs.update(overrides)
    return teacher_import.create_teacher_account(harness, **kwargs)


# ── the proof: one hiccup, exactly one teacher, nothing left behind ───────────

def test_one_hiccup_creates_the_teacher_exactly_once():
    harness = LiveGoTrue(hiccups=1)

    uid = _create_teacher(harness)

    # The hiccup was seen and repeated...
    assert harness.attempts == 2, "the transient answer was not retried"
    # ...but only one account was ever written, because the first attempt rolled back.
    assert harness.commits == 1, "the retry produced a second account"
    assert list(harness.by_email) == ["budi@sekolah.invalid"]
    assert uid in harness.users and len(harness.users) == 1
    # And the whole account is that one: profile and role row, both the same uid.
    assert set(harness.tables["profiles"]) == {uid}
    assert set(harness.tables["teachers"]) == {uid}
    assert harness.tables["profiles"][uid]["role"] == "guru"
    assert harness.tables["teachers"][uid]["employee_id"] == "12345"
    # Nothing was rolled back, because after the retry there was nothing to undo.
    assert harness.deleted == []


def test_the_hiccup_is_on_record_for_the_status_page():
    """A retry that succeeds is exactly the invisible event the card exists for."""
    harness = LiveGoTrue(hiccups=1)

    _create_teacher(harness)

    reading = auth_health.state()
    assert reading["worker_retries"] == 1
    assert reading["worker_exhausted"] == 0
    assert GENERIC.lower() in (reading["worker_reason"] or "").lower()
    # The cross-worker marker was left too, and the box reads as amber, not clean.
    assert reading["recorded"] is True
    assert reading["key"] == auth_health.RECORDED
    assert reading["marker"]["present"] is True


# ── the counterfactual: the harness can tell rollback from leftover ───────────

def test_the_harness_distinguishes_a_rollback_from_a_landed_attempt():
    """Without this, `commits == 1` would pass for the wrong reason.

    The argument for retrying is that GoTrue rolled back, so nothing exists to
    duplicate. A server that *did* write before failing would leave a row -- and
    the harness sees it. That difference is the assumption, made testable.
    """
    rolled_back = LiveGoTrue(hiccups=1)
    with pytest.raises(Exception, match="creating new user"):
        rolled_back.auth.admin.create_user({"email": "x@sekolah.invalid"})
    assert rolled_back.commits == 0 and not rolled_back.users

    landed = LiveGoTrue(hiccups=1, landed_on_hiccup=True)
    with pytest.raises(Exception, match="creating new user"):
        landed.auth.admin.create_user({"email": "x@sekolah.invalid"})
    assert landed.commits == 1, "the harness cannot see a write that survived a failure"


def test_the_harness_refuses_a_second_account_for_the_same_email():
    """The uniqueness GoTrue enforces, so a duplicate cannot hide in a store."""
    harness = LiveGoTrue()
    harness.auth.admin.create_user({"email": "a@sekolah.invalid"})

    with pytest.raises(Exception, match="already been registered"):
        harness.auth.admin.create_user({"email": "a@sekolah.invalid"})

    assert harness.commits == 1 and len(harness.users) == 1


# ── the failure paths: no orphan, no duplicate, no half-account ───────────────

def test_a_permanent_hiccup_leaves_no_account_and_no_orphan():
    harness = LiveGoTrue(hiccups=99)

    with pytest.raises(ScanGradeException) as caught:
        _create_teacher(harness)

    # Bounded: it stops, it does not hammer.
    assert harness.attempts == 3
    # Every attempt rolled back, so there is no account, no half-account, and
    # nothing an undo could have removed.
    assert harness.commits == 0
    assert harness.users == {} and harness.by_email == {}
    assert not harness.tables.get("profiles")
    assert not harness.tables.get("teachers")
    assert harness.deleted == []
    # And the operator is told the two facts the generic wording hides.
    assert "belum dibuat" in caught.value.user_message.lower()
    assert GENERIC in caught.value.message


def test_a_role_write_that_fails_after_a_retried_create_removes_the_single_account():
    """Retry and rollback together: one account made, then unmade, leaving none."""
    harness = LiveGoTrue(hiccups=1, fail_tables={"teachers"})

    with pytest.raises(RuntimeError):
        _create_teacher(harness)

    assert harness.attempts == 2 and harness.commits == 1
    # The teacher row could not be written, so the one account was undone by its
    # cascade -- no sign-in-able user with no role, and no second account.
    assert harness.deleted == ["uid-2"]
    assert harness.users == {} and harness.by_email == {}
    assert not harness.tables.get("profiles")
    assert not harness.tables.get("teachers")


def test_a_landed_account_is_never_retried_and_never_duplicated():
    """"Already registered" is the school's data, not a hiccup -- and one attempt."""
    harness = LiveGoTrue()
    harness.auth.admin.create_user({"email": "budi@sekolah.invalid"})
    before = harness.commits

    with pytest.raises(Exception, match="already been registered"):
        _create_teacher(harness)

    assert harness.commits == before, "a refusal was repeated anyway"
    assert harness.users and len(harness.users) == 1, "the existing account was disturbed"
    assert harness.deleted == []
