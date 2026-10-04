"""Satu pembuat akun — supaya retry, rollback, dan aturan password sekali pakai
tidak bisa lagi saling menjauh.

Tiga service dulu menuliskan tiga kewajiban yang sama, masing-masing di filenya
sendiri:

* naik ``auth_retry.create_user_with_retry``, karena jawaban database GoTrue
  yang generik adalah server yang tersedak, bukan datanya yang salah;
* menghapus auth user kalau baris berikutnya gagal, supaya tidak ada akun yang
  bisa login tapi tidak dimiliki peran mana pun;
* menstempel ``password_change.account_fields``, karena password yang dibuat di
  sini adalah password sekali pakai.

Tiga salinan berarti tiga kesempatan untuk lupa. Sekarang kewajibannya tinggal di
satu modul — ``app.services.account_creation`` — dan tiap pembuat hanya
menyumbang baris khusus perannya. File ini penjaganya, dan ia membaca pohon
sumber (bukan daftar di dalam tes), jadi ``create_user`` langsung di salah satu
dari ketiganya adalah hal yang membuatnya gagal.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The three account creators a school admin drives, and the files they live in.
CREATOR_FILES = {
    "app/services/teacher_import.py",
    "app/services/student_import.py",
    "app/services/school_officials.py",
}

#: The one module allowed to talk to GoTrue, undo a half-made account, or stamp
#: an account as one-time-password.
SERVICE = "app/services/account_creation.py"


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _call_names(source: str) -> set[str]:
    """Every called function name in ``source``, as ``create_user`` would be."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            else:
                names.add(getattr(node.func, "id", ""))
    return names


def _modules_calling(name: str) -> set[str]:
    """The app modules whose source *calls* ``name``.

    Read off the tree rather than a list, because that is what catches a new
    creator copied from an old one.
    """
    found = set()
    for path in sorted((ROOT / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        if name in _call_names(source):
            found.add(str(path.relative_to(ROOT)).replace("\\", "/"))
    return found


# ── the shared primitive exists ──────────────────────────────────────────────

def test_the_shared_account_primitive_can_be_imported():
    from app.services.account_creation import create_account

    assert callable(create_account)


# ── the three creators delegate, they do not create ──────────────────────────

def test_no_creator_calls_create_user_directly_any_more():
    offenders = sorted(rel for rel in CREATOR_FILES
                       if "create_user" in _call_names(_source(rel)))
    assert not offenders, (
        "these creators talk to GoTrue directly again; route them through "
        "app.services.account_creation.create_account():\n  "
        + "\n  ".join(offenders))


def test_every_creator_calls_the_shared_primitive():
    missing = sorted(rel for rel in CREATOR_FILES
                     if "create_account" not in _call_names(_source(rel)))
    assert not missing, (
        "these creators do not use the shared account primitive:\n  "
        + "\n  ".join(missing))


# ── the retry, the rollback, the password rule: one caller each ──────────────

def test_only_the_shared_module_calls_the_retry():
    assert _modules_calling("create_user_with_retry") == {SERVICE}, (
        "the retry is a habit of more than one module again")


def test_only_the_shared_module_stamps_the_one_time_password():
    assert _modules_calling("account_fields") == {SERVICE}, (
        "more than one module writes the issued-password fields")


#: The create function each creator file exports (the function under audit).
CREATE_FUNCS = {
    "app/services/teacher_import.py": "create_teacher_account",
    "app/services/student_import.py": "create_student_account",
    "app/services/school_officials.py": "create_official",
}


def _func_calls(source: str, func: str) -> set[str]:
    """The function names called inside ``func`` only (not the whole module)."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return _call_names(ast.unparse(node))
    raise AssertionError(f"{func}() not found")


def test_no_creator_rolls_back_on_its_own():
    """A creator must not undo a half-made account itself.

    Scoped to the create function, because ``delete_official`` legitimately
    removes an official — it is not a rollback of a create.
    """
    offenders = sorted(
        rel for rel, func in CREATE_FUNCS.items()
        if {"delete_user", "discard", "discard_partial_account", "rollback"}
        & _func_calls(_source(rel), func))
    assert not offenders, (
        "these creators roll back on their own instead of through the shared "
        "primitive:\n  " + "\n  ".join(offenders))


# ── behaviour: one create lays down auth user, profile and role row ──────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store, self.table, self.payload = store, table, None

    def upsert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        self.store.writes.append((self.table, dict(self.payload)))
        return _Resp([self.payload])


class _Admin:
    def __init__(self, store):
        self.store = store

    def create_user(self, attributes):
        self.store.creates.append(attributes)
        return type("Created", (), {
            "user": type("U", (), {"id": f"uid-{len(self.store.creates)}"})()})()

    def delete_user(self, uid, should_soft_delete=False):
        self.store.deleted.append(uid)


class _Fake:
    def __init__(self):
        self.creates, self.deleted, self.writes = [], [], []
        self.auth = type("Auth", (), {"admin": _Admin(self)})()

    def table(self, name):
        return _Query(self, name)


def test_the_shared_primitive_writes_the_whole_account():
    from app.services.account_creation import create_account

    fake = _Fake()
    uid = create_account(
        fake, school_id="s1", role="guru", full_name="Budi",
        email="budi@example.invalid", password="pw",
        role_table="teachers", role_fields={"employee_id": "123"},
        identifier="123")

    assert uid == "uid-1"
    profile = next(row for table, row in fake.writes if table == "profiles")
    assert profile["id"] == uid and profile["role"] == "guru"
    assert profile["school_id"] == "s1"
    # The issued-password rule is applied by the one primitive, not the caller.
    assert profile["must_change_password"] is True
    assert profile["email"] == "budi@example.invalid"
    teachers = next(row for table, row in fake.writes if table == "teachers")
    assert teachers["id"] == uid and teachers["employee_id"] == "123"
    assert fake.deleted == [], "nothing failed, so nothing was rolled back"


def test_a_failed_role_row_undoes_the_auth_user():
    from app.services.account_creation import create_account

    fake = _Fake()
    original = fake.table

    def fail_teachers(name):
        query = original(name)
        if name == "teachers":
            def explode():
                raise RuntimeError("boom")
            query.execute = explode
        return query

    fake.table = fail_teachers

    try:
        create_account(
            fake, school_id="s1", role="guru", full_name="Budi",
            email="budi@example.invalid", password="pw",
            role_table="teachers", role_fields={"employee_id": "123"})
    except RuntimeError:
        pass
    else:  # pragma: no cover - the point is that it raises
        raise AssertionError("a failing role row must raise")

    assert fake.deleted == ["uid-1"], "the half-made account must be rolled back"
