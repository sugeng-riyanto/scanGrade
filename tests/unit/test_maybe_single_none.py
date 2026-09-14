"""Regression tests for `maybe_single().execute()` returning ``None``.

postgrest >= 0.17 returns ``None`` from ``maybe_single().execute()`` when the
query matches **no** row — it does *not* return a response carrying
``data=None``. Any ``.data`` on that result raises ``AttributeError``.

That one idiom was the reason bulk student import silently created **zero**
students: the duplicate-NISN pre-check queries for a NISN that by definition
does not exist yet, so the very first student hit the ``None`` path. The same
shape appeared at 13 call sites, all on their "not found" branch.

Every such query must now go through ``app.utils.helpers.row_or_none``.
"""
import io
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.utils.helpers import row_or_none

APP_DIR = Path(__file__).resolve().parents[2] / "app"


# ── the helper itself ─────────────────────────────────────────

def test_row_or_none_passes_through_data():
    resp = MagicMock()
    resp.data = {"id": "abc"}
    assert row_or_none(resp) == {"id": "abc"}


def test_row_or_none_maps_none_response_to_none():
    """The whole point: a missing row must be falsy, not an exception."""
    assert row_or_none(None) is None


def test_row_or_none_maps_data_none_to_none():
    resp = MagicMock()
    resp.data = None
    assert row_or_none(resp) is None


# ── the real call site that was broken: bulk student import ───

@pytest.fixture
def import_module():
    from app.services import student_import
    return student_import


class _Resp:
    """postgrest returns a response object on a match, ``None`` on no match."""

    def __init__(self, data):
        self.data = data


class _FakeSupabase:
    """Minimal stand-in for the client chains used by the import service.

    ``existing`` and ``class_row`` deliberately default to ``None``, mirroring
    what postgrest really returns when no row matches.
    """

    def __init__(self, existing=None, class_row=None, created_ids=None):
        # `existing` / `class_row` are plain rows or None.
        #
        # The two lookup shapes must be modelled deliberately, because a bare
        # MagicMock answers *every* chain with a truthy mock: an unconfigured
        # one makes a lookup look like a hit, so a "no duplicate, import it"
        # test would fail and a "duplicate, refuse it" test would pass without
        # ever exercising the real branch.
        #   .limit(1).execute()            -> a LIST of rows (the global lookup)
        #   .maybe_single().execute()       -> the row, or None when nothing matched
        self.existing = existing
        self.class_row = class_row
        self.created_ids = list(created_ids or ["uid-1", "uid-2", "uid-3"])
        self.created_emails = []
        self.upserts = []

    def table(self, name):
        tbl = MagicMock()
        if name == "students":
            tbl.select.return_value.eq.return_value.limit.return_value \
                .execute.return_value = _Resp(
                    [dict(self.existing)] if self.existing is not None else [])
            tbl.select.return_value.eq.return_value.eq.return_value \
                .maybe_single.return_value.execute.return_value = (
                    _Resp(self.existing) if self.existing is not None else None)
        elif name == "classes":
            tbl.select.return_value.eq.return_value.eq.return_value \
                .maybe_single.return_value.execute.return_value = (
                    _Resp(self.class_row) if self.class_row is not None else None)
        tbl.upsert.side_effect = lambda payload, *a, **k: (
            self.upserts.append((name, payload)) or MagicMock()
        )
        return tbl

    @property
    def auth(self):
        auth = MagicMock()

        def _create_user(payload):
            self.created_emails.append(payload["email"])
            user = MagicMock()
            user.user.id = self.created_ids[len(self.created_emails) - 1]
            return user

        auth.admin.create_user.side_effect = _create_user
        return auth


def _csv(*rows):
    out = io.StringIO()
    out.write("nama,nisn,kelas,password\n")
    for nama, nisn in rows:
        out.write(f"{nama},{nisn},LT-KELAS-1,LoadTest123!\n")
    return io.BytesIO(out.getvalue().encode())


def test_import_creates_students_when_duplicate_check_finds_nothing(
    monkeypatch, import_module
):
    """The exact production failure: 0 created / N failed on a fresh import."""
    db = _FakeSupabase(existing=None)
    monkeypatch.setattr(import_module, "get_supabase", lambda: db)

    res = import_module.import_students_from_csv(
        _csv(("LT Murid 1", "900000001"), ("LT Murid 2", "900000002")),
        school_id="school-1",
        class_id="class-1",
    )

    assert res["total"] == 2
    assert res["success"] == 2, res["errors"]
    assert res["failed"] == 0
    assert db.created_emails == [
        "900000001@siswa.scan-grade.app",
        "900000002@siswa.scan-grade.app",
    ]


def test_import_still_rejects_a_duplicate_nisn(monkeypatch, import_module):
    """`row_or_none` must not turn the duplicate guard into a no-op."""
    db = _FakeSupabase(existing={"id": "already-there"})
    monkeypatch.setattr(import_module, "get_supabase", lambda: db)

    res = import_module.import_students_from_csv(
        _csv(("LT Murid 1", "900000001")), school_id="school-1", class_id="class-1"
    )

    assert res["success"] == 0
    assert res["failed"] == 1
    assert "sudah terdaftar" in res["errors"][0]["message"]
    assert db.created_emails == []


def test_import_resolves_class_by_name_when_missing(monkeypatch, import_module):
    """Class lookup is another None-on-no-match site: absent name must not crash."""
    db = _FakeSupabase(existing=None, class_row=None)
    monkeypatch.setattr(import_module, "get_supabase", lambda: db)

    res = import_module.import_students_from_csv(
        _csv(("LT Murid 1", "900000001")), school_id="school-1", class_id=None
    )

    assert res["success"] == 1, res["errors"]
    student_upsert = [p for t, p in db.upserts if t == "students"][0]
    assert student_upsert["class_id"] is None


# ── static guard: keep the dangerous idiom from coming back ───

# An assignment whose right-hand side reaches a ``maybe_single().execute()``
# without going through ``row_or_none``. ``[^=]`` cannot cross another
# assignment, so each match stays inside one statement.
# One chunk of a statement's right-hand side. `=` normally ends the scan — that
# keeps the match inside a single statement — but a keyword argument
# (`.order("created_at", desc=True)`) is part of the same statement and must be
# allowed through, or a real offender would slip past.
_RHS = r"(?:[^=]|\b\w+=(?!=))"

# The scan may not cross a definition. Without this the window is a pure
# proximity heuristic, and an unrelated assignment a few hundred characters
# above a *wrapped* call reads as that call's target: `parsed = json.loads(v)` in
# one helper, then `return row_or_none(...maybe_single().execute())` in the next,
# reported as an unwrapped call that does not exist. A real offender is one
# statement, so it never spans a `def`.
_WINDOW = rf"(?:(?!\ndef ){_RHS}){{0,400}}?"

UNWRAPPED = re.compile(
    # `(?<![=!<>+\-*/%])` / `(?!=)` keep comparison operators (==, !=, ...) from
    # being mistaken for an assignment, and `(?<!\w)` keeps the `=` in a keyword
    # argument from being mistaken for one (it is not followed by a `row_or_none`
    # call, so it would otherwise report a wrapped call as unwrapped).
    rf"(?<![=!<>+\-*/%])(?<!\w)=(?!=)\s*(?!\s*row_or_none\b){_WINDOW}maybe_single\(\)\s*\.execute\(\)",
    re.DOTALL,
)


def test_no_unwrapped_maybe_single_in_app_code():
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in UNWRAPPED.finditer(text):
            line = text[: match.start()].count("\n") + 1
            snippet = " ".join(match.group(0).split())
            offenders.append(f"{path.relative_to(APP_DIR.parent)}:{line}: {snippet[:110]}")

    assert not offenders, (
        "maybe_single().execute() returns None when no row matches — wrap the "
        "call in app.utils.helpers.row_or_none() instead of using .data:\n  "
        + "\n  ".join(offenders)
    )


def test_static_guard_actually_detects_the_bad_idiom(tmp_path):
    """Prove the guard above is not vacuous."""
    bad = (
        'x = supabase.table("students").select("id")\n'
        '    .eq("nisn", nisn).maybe_single().execute()\n'
        "if x.data:\n"
        "    pass\n"
    )
    assert UNWRAPPED.search(bad)
    assert UNWRAPPED.search('y = db.table("t").maybe_single().execute()')
    good = 'x = row_or_none(supabase.table("students").maybe_single().execute())\n'
    assert not UNWRAPPED.search(good)
    # A wrapped call is safe however its query is built — including the
    # `desc=True` keyword argument, which must not read as an assignment.
    wrapped_kwarg = (
        'x = row_or_none(\n'
        '    supabase.table("t").select("code").eq("id", 1)\n'
        '    .order("created_at", desc=True).limit(1).maybe_single().execute()\n'
        ')\n'
    )
    assert not UNWRAPPED.search(wrapped_kwarg)
    # ...while the same query left unwrapped is still caught.
    assert UNWRAPPED.search(
        'x = supabase.table("t").select("code").eq("id", 1)\n'
        '    .order("created_at", desc=True).limit(1).maybe_single().execute()\n'
    )
    # a ternary packed with == must not look like an assignment
    ternary = 'rec = row_or_none(\n    q.eq("a" if t == "one" else "b", x).maybe_single().execute()\n)\n'
    assert not UNWRAPPED.search(ternary)

    # An assignment in one function is not the caller of a call in the next one.
    # This is the shape that produced a false positive: a JSON helper sitting a
    # few hundred characters above the fetch helpers, whose every call *is*
    # wrapped. The window must stop at the `def`.
    neighbouring_functions = (
        "def as_dict(value):\n"
        "    if isinstance(value, str):\n"
        "        parsed = json.loads(value)\n"
        "        return parsed\n"
        "    return {}\n"
        "\n"
        "\n"
        "def fetch(supabase, uid):\n"
        "    return row_or_none(supabase.table('p').select('id').eq('id', uid).maybe_single().execute())\n"
    )
    assert len(neighbouring_functions) < 400, "keep the case inside the scan window"
    assert not UNWRAPPED.search(neighbouring_functions)

    # ...and an offender inside a function is still caught, so the clause above
    # cannot be used to hide a real one behind a `def`.
    offender_in_a_function = (
        "def as_dict(value):\n"
        "    parsed = json.loads(value)\n"
        "    return parsed\n"
        "\n"
        "\n"
        "def fetch(supabase, uid):\n"
        "    row = supabase.table('p').select('id').eq('id', uid).maybe_single().execute()\n"
        "    return row.data\n"
    )
    assert UNWRAPPED.search(offender_in_a_function)
