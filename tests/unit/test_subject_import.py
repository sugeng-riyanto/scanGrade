"""The "Mata Pelajaran" sheet import: no more 23505, no more duplication.

Both defects were reproduced against the live database before being fixed.

1. A sheet with no code column wrote ``code=""``. The unique index is
   ``(school_id, code) WHERE code IS NOT NULL`` and ``'' IS NOT NULL``, so an
   empty string sits *inside* the index — the first subject imported, and the
   second died: ``409 23505 duplicate key value violates unique constraint
   "idx_subjects_school_code"``.

2. The upsert payload carried no ``id`` while the primary key *is* ``id``, so the
   conflict target could never match and the statement was a plain INSERT.
   Re-importing a sheet therefore duplicated every subject instead of updating it.
   Note ``on_conflict="school_id,code"`` does NOT work here: PostgREST answers
   ``42P10`` because a partial index cannot serve as a conflict arbiter.

``FakeSubjectsTable`` enforces the same partial unique index as Postgres, so
these tests fail the way production fails rather than merely on shape.
"""
import pytest

from app.routes.admin_sekolah import _import_subjects

SCHOOL = "11111111-1111-1111-1111-111111111111"
OTHER_SCHOOL = "22222222-2222-2222-2222-222222222222"


# ── stand-ins ────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Table:
    """Chainable proxy: select(...).eq(...).execute() / upsert(...).execute()."""

    def __init__(self, store):
        self.store = store
        self.filters = {}
        self.pending = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def upsert(self, payload):
        self.pending = payload
        return self

    def execute(self):
        if self.pending is not None:
            payload, self.pending = self.pending, None
            return _Resp([self.store.write(payload)])
        return _Resp([
            dict(r) for r in self.store.rows
            if all(r.get(k) == v for k, v in self.filters.items())
        ])


class FakeSubjectsTable:
    """In-memory `subjects`, including the live partial unique index."""

    def __init__(self, rows=()):
        self.rows = [dict(r) for r in rows]
        self.upserts = []

    def table(self, name):
        assert name == "subjects", f"unexpected table {name!r}"
        return _Table(self)

    def write(self, payload):
        self.upserts.append(dict(payload))
        row = dict(payload)

        if row.get("id"):
            for existing in self.rows:
                if existing["id"] == row["id"]:
                    existing.update({k: v for k, v in row.items() if k != "id"})
                    return dict(existing)

        # INSERT. UNIQUE (school_id, code) WHERE code IS NOT NULL — an empty
        # string is NOT NULL, so it participates and collides.
        code = row.get("code")
        if code is not None:
            for existing in self.rows:
                if (existing.get("school_id") == row.get("school_id")
                        and existing.get("code") == code):
                    raise RuntimeError(
                        "duplicate key value violates unique constraint "
                        '"idx_subjects_school_code"'
                    )

        row["id"] = f"subj-{len(self.rows) + 1}"
        self.rows.append(row)
        return dict(row)


class FakeSheet:
    """Just enough worksheet for ``iter_rows(min_row=2, values_only=True)``."""

    def __init__(self, rows):
        self.rows = rows

    def iter_rows(self, min_row=2, values_only=True):
        assert values_only
        yield from self.rows[min_row - 1:]


def run_import(sheet_rows, preexisting=(), sid=SCHOOL):
    table = FakeSubjectsTable(preexisting)
    results = {"students": 0, "teachers": 0, "subjects": 0,
               "subjects_updated": 0, "errors": []}
    _import_subjects(FakeSheet(sheet_rows), sid, table, results)
    return table, results


def codes(table):
    return [r.get("code") for r in table.rows]


# ── 1. a sheet with no code column ───────────────────────────────────────────

def test_sheet_without_code_column_imports_every_row():
    """One-column sheet: every row must land, none may be rejected."""
    sheet = [
        ("Mata Pelajaran",),
        ("Matematika",),
        ("Fisika",),
        ("Biologi",),
    ]
    table, results = run_import(sheet)

    assert results["errors"] == []
    assert results["subjects"] == 3
    assert len(table.rows) == 3
    assert codes(table) == [None, None, None], (
        "a blank code must be NULL; '' IS NOT NULL would collide on the second row"
    )


def test_explicitly_empty_code_cells_do_not_collide():
    """The exact shape that returned 409 against production."""
    sheet = [
        ("Mata Pelajaran", "Kode"),
        ("Matematika", None),
        ("Fisika", None),
    ]
    table, results = run_import(sheet)

    assert results["errors"] == []
    assert len(table.rows) == 2
    assert codes(table) == [None, None]
    assert all(u.get("code") != "" for u in table.upserts), "never write '' as a code"


def test_first_row_is_not_spent_on_a_rejected_second():
    """Regression: the old code imported row 1 then failed from row 2 on."""
    sheet = [("Mata Pelajaran", "Kode")] + [(f"Mapel {i}", None) for i in range(1, 6)]
    table, results = run_import(sheet)

    assert results["errors"] == []
    assert len(table.rows) == 5


# ── 2. re-importing must update, not duplicate ───────────────────────────────

def test_reimport_updates_instead_of_duplicating():
    sheet = [
        ("Mata Pelajaran", "Kode"),
        ("Matematika", "MTK"),
        ("Fisika", "FIS"),
    ]
    table, first = run_import(sheet)
    ids_after_first = {r["name"]: r["id"] for r in table.rows}

    table, second = run_import(sheet, preexisting=table.rows)

    assert first["errors"] == [] and second["errors"] == []
    assert len(table.rows) == 2, "a re-import must not add rows"
    assert {r["name"]: r["id"] for r in table.rows} == ids_after_first, (
        "the existing rows must be the ones updated"
    )
    assert second["subjects_updated"] == 2
    assert second["subjects"] == 2


def test_reimport_after_a_rename_updates_the_same_row():
    sheet = [("Mata Pelajaran", "Kode"), ("Matematika", "MTK")]
    table, _ = run_import(sheet)
    original_id = table.rows[0]["id"]

    renamed = [("Mata Pelajaran", "Kode"), ("Matematika Lanjut", "MTK")]
    table, results = run_import(renamed, preexisting=table.rows)

    assert results["errors"] == []
    assert len(table.rows) == 1
    assert table.rows[0]["id"] == original_id
    assert table.rows[0]["name"] == "Matematika Lanjut"


def test_blank_code_does_not_wipe_a_stored_code():
    """The sheet is silent, not authoritative — keep what the school set."""
    preexisting = [{"id": "subj-x", "school_id": SCHOOL, "name": "Matematika",
                    "code": "MTK"}]
    sheet = [("Mata Pelajaran", "Kode"), ("Matematika", None)]

    table, results = run_import(sheet, preexisting=preexisting)

    assert results["errors"] == []
    assert len(table.rows) == 1
    assert table.rows[0]["code"] == "MTK", "a blank cell must not erase the code"
    assert table.rows[0]["id"] == "subj-x"


def test_repeated_row_inside_one_sheet_is_collapsed():
    sheet = [("Mata Pelajaran", "Kode"), ("Matematika", None), ("Matematika", None)]

    table, results = run_import(sheet)

    assert results["errors"] == []
    assert len(table.rows) == 1


def test_same_code_at_another_school_is_untouched():
    """The index is per school; a colliding code elsewhere must not block this."""
    preexisting = [{"id": "subj-other", "school_id": OTHER_SCHOOL,
                    "name": "Matematika", "code": "MTK"}]
    sheet = [("Mata Pelajaran", "Kode"), ("Matematika", "MTK")]

    table, results = run_import(sheet, preexisting=preexisting)

    assert results["errors"] == []
    assert len(table.rows) == 2
    assert len({r["school_id"] for r in table.rows}) == 2


# ── 3. payload shape ─────────────────────────────────────────────────────────

def test_every_write_is_scoped_to_the_school():
    sheet = [("Mata Pelajaran", "Kode"), ("Matematika", None)]
    table, _ = run_import(sheet)
    assert all(u["school_id"] == SCHOOL for u in table.upserts)


def test_a_missing_lookup_is_reported_but_not_fatal():
    """If the pre-load fails we must still import, and say so."""
    class Exploding(FakeSubjectsTable):
        def table(self, name):
            raise RuntimeError("connection reset")

    results = {"students": 0, "teachers": 0, "subjects": 0,
               "subjects_updated": 0, "errors": []}
    _import_subjects(FakeSheet([("Mata Pelajaran",), ("Matematika",)]),
                     SCHOOL, Exploding(), results)

    assert any("Gagal membaca daftar mapel" in e for e in results["errors"])


def test_blank_rows_are_skipped():
    sheet = [("Mata Pelajaran", "Kode"), (None, None), ("   ", None), ("Fisika", None)]
    table, results = run_import(sheet)

    assert results["errors"] == []
    assert len(table.rows) == 1
    assert table.rows[0]["name"] == "Fisika"


@pytest.mark.parametrize("code_cell", ["", None, "   "])
def test_whitespace_only_codes_become_null(code_cell):
    sheet = [("Mata Pelajaran", "Kode"), ("Matematika", code_cell)]
    table, results = run_import(sheet)

    assert results["errors"] == []
    assert codes(table) == [None]
