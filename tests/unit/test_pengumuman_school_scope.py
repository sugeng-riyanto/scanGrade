"""Pengumuman must not cross school boundaries.

Measured on the live database before migration 024: two schools published one
announcement each, both landed on `school_id = 1` (the value the INT column
accepted, written by a retry in `api_create_pengumuman`), and the read path's
`.eq("school_id", 1)` returned both. That is a cross-tenant leak.

There was a second, quieter half: the read path probed the column and fell back
to `school_id = 1` whenever the probe came back *empty*, so a school with no
announcements of its own read the shared bucket — the fallback fired on a correct
result, not just an error.

These tests drive the real view functions with `g` populated, against a fake
table that scopes rows the way the database now does, and assert the school
filter actually applied is the caller's own school.
"""
import json

import pytest

from tests.conftest import app_instance
from app.routes import api as api_module
from app.routes.api import api_create_pengumuman, api_list_pengumuman

SCHOOL_A = "aaaaaaaa-0000-0000-0000-00000000000a"
SCHOOL_B = "bbbbbbbb-0000-0000-0000-00000000000b"
SCHOOL_C = "cccccccc-0000-0000-0000-00000000000c"

GURU_A = "11111111-0000-0000-0000-00000000000a"
GURU_B = "22222222-0000-0000-0000-00000000000b"
GURU_C = "33333333-0000-0000-0000-00000000000c"


# ── a fake that scopes rows the way the schema does ──────────────────────────

class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def _matches(row, col, val):
    actual = row.get(col)
    if col == "is_archived":
        return bool(actual) == (val not in ("false", False, "False"))
    return str(actual) == str(val)


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.filters = []
        self.payload = None
        self.op = "select"
        self.want_count = False

    def select(self, *_a, count=None, **_k):
        self.want_count = bool(count)
        return self

    def is_(self, col, val):
        self.filters.append((col, val))
        return self

    def eq(self, col, val):
        if col == "school_id":
            self.store.school_filters.append(val)
        self.filters.append((col, val))
        return self

    def or_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def offset(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def execute(self):
        if self.op == "insert":
            return _Resp([self.store.insert(self.table, self.payload)])
        rows = [
            dict(r) for r in self.store.rows_for(self.table)
            if all(_matches(r, c, v) for c, v in self.filters)
        ]
        return _Resp(rows, count=len(rows) if self.want_count else None)


class FakeSupabase:
    def __init__(self, tables=None, fail_insert=None):
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.school_filters = []
        self.inserts = []
        self.fail_insert = fail_insert

    def table(self, name):
        return _Query(self, name)

    def rows_for(self, name):
        return self.tables.setdefault(name, [])

    def insert(self, name, payload):
        if self.fail_insert:
            raise RuntimeError(self.fail_insert)
        self.inserts.append((name, dict(payload)))
        row = dict(payload)
        row.setdefault("id", f"new-{len(self.inserts)}")
        self.tables.setdefault(name, []).append(row)
        return row


def run(view, monkeypatch, fake, *, role="guru", uid=GURU_A, school=SCHOOL_A,
        method="GET", path="/api/pengumuman", payload=None):
    """Call the real view with `g` populated. Returns (body, status_code).

    Views that return ``jsonify(...), 400`` hand back a tuple, so normalise here
    rather than making every test remember which shape it got.
    """
    monkeypatch.setattr(api_module, "get_supabase", lambda: fake)
    app = app_instance()
    with app.test_request_context(
        path, method=method,
        data=json.dumps(payload) if payload else None,
        content_type="application/json" if payload else None,
    ):
        from flask import g

        g.user_id = uid
        g.user_role = role
        g.user_school_id = school
        result = view.__wrapped__()

    if isinstance(result, tuple):
        return result[0], result[1]
    return result, result.status_code


def announcements(*rows):
    return {
        "pengumuman": [
            {
                "id": f"p{i}", "title": title, "school_id": school,
                "sender_id": guru, "target_role": "murid", "is_archived": False,
                "specific_recipients": None, "created_at": "2026-01-01T00:00:00Z",
            }
            for i, (title, school, guru) in enumerate(rows)
        ],
        "pengumuman_read": [],
    }


# ── the leak ────────────────────────────────────────────────────────────────

def test_a_school_sees_only_its_own_announcements(app, monkeypatch):
    fake = FakeSupabase(announcements(
        ("UntuksA", SCHOOL_A, GURU_A),
        ("UntukB", SCHOOL_B, GURU_B),
    ))
    body, status = run(api_list_pengumuman, monkeypatch, fake)

    assert status == 200
    titles = [p["title"] for p in body.get_json()]
    assert titles == ["UntuksA"], "school A must not receive school B's announcement"


def test_the_school_filter_applied_is_the_callers_own_uuid(app, monkeypatch):
    fake = FakeSupabase(announcements(("UntuksA", SCHOOL_A, GURU_A)))
    run(api_list_pengumuman, monkeypatch, fake)

    assert SCHOOL_A in fake.school_filters
    assert 1 not in fake.school_filters, "the legacy shared bucket must never be used"


def test_an_empty_school_does_not_read_the_shared_bucket(app, monkeypatch):
    """The old probe treated 'no rows' as 'wrong column type' and fell back to 1."""
    fake = FakeSupabase(announcements(
        ("UntuksA", SCHOOL_A, GURU_A),
        ("UntukB", SCHOOL_B, GURU_B),
    ))
    body, _status = run(api_list_pengumuman, monkeypatch, fake,
                        uid=GURU_C, school=SCHOOL_C)

    assert body.get_json() == [], "school C has no announcements; it must see none"
    assert SCHOOL_C in fake.school_filters
    assert 1 not in fake.school_filters


def test_super_admin_still_sees_across_schools(app, monkeypatch):
    fake = FakeSupabase(announcements(
        ("UntuksA", SCHOOL_A, GURU_A),
        ("UntukB", SCHOOL_B, GURU_B),
    ))
    body, _status = run(api_list_pengumuman, monkeypatch, fake,
                        role="super_admin", uid="9999", school=None)

    assert sorted(p["title"] for p in body.get_json()) == ["UntukB", "UntuksA"]


# ── the write path ──────────────────────────────────────────────────────────

def test_create_stamps_the_senders_own_school(app, monkeypatch):
    fake = FakeSupabase()
    _body, status = run(api_create_pengumuman, monkeypatch, fake, method="POST",
                        school=SCHOOL_A, payload={
                            "title": "Ujian", "content": "besok",
                            "target_role": "murid",
                            "school_id": SCHOOL_B,  # caller must not get to choose
                        })

    assert status == 201
    _, payload = fake.inserts[0]
    assert payload["school_id"] == SCHOOL_A


def test_create_does_not_retry_with_one_on_a_type_error(app, monkeypatch):
    """The old code wrote school_id = 1 on 'invalid input syntax for type integer'."""
    fake = FakeSupabase(fail_insert="invalid input syntax for type integer:")
    body, status = run(api_create_pengumuman, monkeypatch, fake, method="POST",
                       payload={"title": "Ujian", "content": "besok",
                                "target_role": "murid"})

    assert status == 500
    assert fake.inserts == [], "no row may be written with a fabricated school"
    assert "Gagal" in body.get_json()["error"]


def test_create_without_a_school_is_refused(app, monkeypatch):
    fake = FakeSupabase()
    _body, status = run(api_create_pengumuman, monkeypatch, fake, method="POST",
                        school=None,
                        payload={"title": "Ujian", "content": "besok",
                                 "target_role": "murid"})

    assert status == 400
    assert fake.inserts == [], "an unscoped row would be invisible, not sent"


# ── static guard ────────────────────────────────────────────────────────────

def test_the_shared_bucket_idiom_is_gone_from_the_source():
    """Guard by parsing, not by substring: a plain `in` also matches the comment
    that explains why the fallback was removed."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "app" / "routes" / "api.py"
    text = src.read_text(encoding="utf-8-sig")
    assert "use_int_filter" not in text

    offenders = []
    for node in ast.walk(ast.parse(text)):
        # .eq("school_id", 1)  -- the legacy shared bucket
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "eq"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "school_id"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == 1):
            offenders.append(f"eq(school_id, 1) at line {node.lineno}")
        # payload["school_id"] = 1
        if (isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and node.value.value == 1
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].slice, ast.Constant)
                and node.targets[0].slice.value == "school_id"):
            offenders.append(f'school_id = 1 at line {node.lineno}')

    assert offenders == [], f"the shared bucket idiom is back: {offenders}"
