"""A teacher the operator is told they cannot create, because the server hiccupped.

Reported live from `/admin-sekolah/teachers`:

    Gagal: Database error creating new user

That string is GoTrue's, and it is its *generic* database answer: the insert into
``auth.users`` raised and was rolled back. It is not a fact about the teacher being
added and it is not a fact about the form — the same request succeeds a moment later,
which is exactly what a probe against the live project showed (three shapes of
``create_user`` all landed, and the route itself created and then deleted an account).

So the defect is not "the teacher is bad"; it is that a **transient** server failure is
reported as a permanent one. The fix has two halves, and both are pinned here:

* the create is retried a bounded number of times when the answer is that generic
  database message, because a create is safe to repeat -- GoTrue returns that message
  *after* the row failed and rolled back, so a retry cannot make two accounts;
* if it still will not land, the reader is given a sentence that says the account was
  not made and that retrying is the answer, instead of GoTrue's internal wording.

The three account creators must actually go through this, which is checked by
behaviour: each is driven with a fake that hiccups once.
"""
import ast
from pathlib import Path

import pytest

from app.errors import ScanGradeException

ROOT = Path(__file__).resolve().parents[2]


# ── the message GoTrue answers with, and the ones it must not ────────────────

@pytest.fixture()
def retry_module():
    from app.utils import auth_retry

    return auth_retry


def test_the_generic_database_answer_is_transient(retry_module):
    for message in ("Database error creating new user",
                    "Database error saving new user"):
        assert retry_module.is_transient(Exception(message)) is True


def test_a_real_refusal_is_not_transient(retry_module):
    """"already registered" is a fact about the school's data, not a hiccup.

    Repeating it would only waste three round trips, and worse, it would make the
    operator wait for an answer that will never change.
    """
    for message in ("A user with this email address has already been registered",
                    "Unable to validate email address: invalid format",
                    "User already registered"):
        assert retry_module.is_transient(Exception(message)) is False


# ── the retry itself ─────────────────────────────────────────────────────────

def test_a_hiccup_is_retried_and_then_succeeds(retry_module):
    calls = []

    def attempt():
        calls.append(1)
        if len(calls) == 1:
            raise Exception("Database error creating new user")
        return "the-created-user"

    slept = []
    result = retry_module.create_user_with_retry(
        attempt, sleep=slept.append)

    assert result == "the-created-user"
    assert len(calls) == 2, "the first hiccup did not produce a second attempt"
    assert slept, "a retry without any pause just hammers the server"


def test_a_refusal_the_server_means_is_not_retried(retry_module):
    calls = []

    def attempt():
        calls.append(1)
        raise Exception("A user with this email address has already been registered")

    with pytest.raises(Exception, match="already been registered"):
        retry_module.create_user_with_retry(attempt, sleep=lambda _s: None)

    assert len(calls) == 1, "a permanent refusal was repeated anyway"


def test_exhausting_the_attempts_raises_a_sentence_a_person_can_act_on(retry_module):
    attempts = []

    def attempt():
        attempts.append(1)
        raise Exception("Database error creating new user")

    with pytest.raises(ScanGradeException) as caught:
        retry_module.create_user_with_retry(attempt, sleep=lambda _s: None)

    assert len(attempts) == retry_module.ATTEMPTS
    sentence = caught.value.user_message
    # It has to say the account was not made and that repeating is the answer --
    # the two things GoTrue's wording leaves the operator to guess.
    assert "belum dibuat" in sentence.lower()
    assert "coba" in sentence.lower()
    # And the clue is not thrown away.
    assert "Database error creating new user" in caught.value.message


# ── the creators must actually ride it ───────────────────────────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _User:
    def __init__(self, uid):
        self.id = uid


class _Created:
    def __init__(self, uid):
        self.user = _User(uid)


class _Admin:
    def __init__(self, store):
        self.store = store

    def create_user(self, attributes):
        self.store.creates.append(attributes)
        if len(self.store.creates) <= self.store.hiccups:
            raise Exception("Database error creating new user")
        return _Created(f"uid-{len(self.store.creates)}")

    def delete_user(self, uid, should_soft_delete=False):
        self.store.deleted.append(uid)


class _Query:
    def __init__(self, store, table):
        self.store, self.table, self.payload = store, table, None

    def upsert(self, payload):
        self.payload = payload
        return self

    def insert(self, payload):
        self.payload = payload
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        self.store.writes.append((self.table, self.payload))
        return _Resp([self.payload])


class _Auth:
    def __init__(self, store):
        self.admin = _Admin(store)


class FakeSupabase:
    def __init__(self, hiccups=1):
        self.creates = []
        self.deleted = []
        self.writes = []
        self.hiccups = hiccups
        self.auth = _Auth(self)

    def table(self, name):
        return _Query(self, name)


def test_the_teacher_creator_survives_one_hiccup():
    from app.services import teacher_import

    sb = FakeSupabase(hiccups=1)
    uid = teacher_import.create_teacher_account(
        sb, school_id="s1", full_name="Budi", email="budi@example.invalid",
        password="Probe1234!", employee_id="12345")

    assert uid == "uid-2", "the teacher creator did not retry the hiccup"
    assert len(sb.creates) == 2
    assert not sb.deleted, "nothing was rolled back, because nothing failed"


def test_the_student_creator_survives_one_hiccup():
    from app.services import student_import

    sb = FakeSupabase(hiccups=1)
    uid = student_import.create_student_account(
        sb, school_id="s1", nisn="1000000001", full_name="Bella",
        email="bella@example.invalid", password="Probe1234!", class_id="c1")

    assert uid == "uid-2", "the student creator did not retry the hiccup"
    assert len(sb.creates) == 2


def test_the_official_creator_survives_one_hiccup():
    from app.services import school_officials

    sb = FakeSupabase(hiccups=1)
    uid = school_officials.create_official(
        sb, school_id="s1", role="principal", full_name="Kepala",
        email="kepala@example.invalid", password="Probe1234!")

    assert uid == "uid-2", "the official creator did not retry the hiccup"
    assert len(sb.creates) == 2


# ── the retry is not a private habit of one file ─────────────────────────────

CREATE_SITES = {
    "app/services/teacher_import.py",
    "app/services/student_import.py",
    "app/services/school_officials.py",
}


def test_every_account_creator_routes_its_create_through_the_retry():
    """A fourth creator copied from these must not skip the retry by accident.

    Read off the tree rather than from a list in this test, so a new
    ``create_user`` in a service is what makes this fail.
    """
    missing = []
    for rel in sorted(CREATE_SITES):
        source = (ROOT / rel).read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        direct = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "create_user"
        ]
        assert direct, f"{rel} no longer calls create_user at all"
        if "create_user_with_retry(" not in source:
            missing.append(rel)

    assert not missing, (
        "these creators can still answer GoTrue's generic database error as if it "
        "were a fact about the school's data:\n  " + "\n  ".join(missing))
