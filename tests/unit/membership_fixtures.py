"""An in-memory stand-in for the Supabase client the membership code talks to.

Written once and shared, because three test files need the same three properties
and a second copy of a fake is where one file's fixture quietly stops being the
same database as another's:

* **filters compose in the order the caller writes them** (`eq`, `in_`, `order`,
  `limit`), including when they come *after* `update`/`upsert` — which is how
  supabase-py is used here (`.update({...}).eq(...).eq(...).execute()`);
* **writes are recorded**, so a test can assert the shape a refusal must have:
  an unauthorised reopen leaves *nothing* changed, not merely the wrong status;
* **a table can be made to fail**, at the point the client resolves it, which is
  where supabase-py raises when the table does not exist yet (migration not
  applied) — the failure the "narrow nothing" rule exists for.
"""
from __future__ import annotations

from types import SimpleNamespace


class Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = []
        self.limit_n = None
        self.order_col = None
        self.order_desc = False
        self.payload = None
        self.mode = "select"
        self.conflict = None
        self.columns = None

    # ── the read chain ──────────────────────────────────────────────────────
    def select(self, columns="*", **kw):
        self.columns = columns
        if self.mode == "select":
            self.db.reads.append(self.table)
        return self

    def eq(self, column, value):
        self.filters.append((column, str(value)))
        return self

    def in_(self, column, values):
        wanted = {str(v) for v in values}
        self.filters.append((column, wanted))
        return self

    def neq(self, column, value):
        self.filters.append((column, None, str(value)))
        return self

    def order(self, column, desc=False):
        self.order_col, self.order_desc = column, desc
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def single(self):
        return self

    # ── the write chain ─────────────────────────────────────────────────────
    def update(self, payload):
        self.mode = "update"
        self.payload = dict(payload)
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = dict(payload)
        return self

    def upsert(self, payload, on_conflict=None):
        self.mode = "upsert"
        self.payload = dict(payload)
        self.conflict = on_conflict
        return self

    # ── the evaluation ──────────────────────────────────────────────────────
    def _matches(self, row) -> bool:
        for f in self.filters:
            column = f[0]
            if len(f) == 3:                      # neq
                if str(row.get(column)) == f[2]:
                    return False
                continue
            value = f[1]
            if isinstance(value, set):           # in_
                if str(row.get(column)) not in value:
                    return False
                continue
            if row.get(column) is None or str(row.get(column)) != value:
                return False
        return True

    def _rows(self):
        return self.db.tables.setdefault(self.table, [])

    def execute(self):
        rows = self._rows()
        if self.mode == "select":
            found = [dict(r) for r in rows if self._matches(r)]
            if self.order_col:
                found.sort(key=lambda r: str(r.get(self.order_col) or ""),
                           reverse=self.order_desc)
            if self.limit_n:
                found = found[:self.limit_n]
            return Result(found)
        if self.mode == "update":
            changed = []
            for row in rows:
                if self._matches(row):
                    row.update(self.payload)
                    changed.append(dict(row))
            if changed:
                # Recorded only when a row actually changed, so `db.writes == []`
                # means "nothing was touched" — which is the shape a refusal must
                # have. An update whose filter matched nothing left no trace, and a
                # test that treats it as a write would be asserting a payload the
                # database never applied.
                self.db.writes.append(("update", self.table, dict(self.payload)))
            return Result(changed)
        if self.mode == "insert":
            new = dict(self.payload, id=self.payload.get("id")
                       or f"{self.table}-{len(rows) + 1}")
            rows.append(new)
            self.db.writes.append(("insert", self.table, dict(self.payload)))
            return Result([dict(new)])
        # upsert: the conflict columns name the row it updates
        keys = [k.strip() for k in (self.conflict or "").split(",") if k.strip()]
        for row in rows:
            if keys and all(str(row.get(k)) == str(self.payload.get(k)) for k in keys):
                row.update(self.payload)
                self.db.writes.append(("upsert", self.table, dict(self.payload)))
                return Result([dict(row)])
        new = dict(self.payload, id=self.payload.get("id")
                   or f"{self.table}-{len(rows) + 1}")
        rows.append(new)
        self.db.writes.append(("upsert", self.table, dict(self.payload)))
        return Result([dict(new)])


class FakeDb:
    """A whole database: the tables, the read log and the write log."""

    def __init__(self, **tables):
        self.tables = {name: list(rows) for name, rows in tables.items()}
        self.reads: list[str] = []
        self.writes: list[tuple] = []
        self.fail: set[str] = set()

    def table(self, name):
        if name in self.fail:
            raise RuntimeError(f'table "{name}" does not exist')
        return FakeQuery(self, name)

    # ── reading the fake's own state ────────────────────────────────────────
    def rows(self, table) -> list[dict]:
        return self.tables.setdefault(table, [])

    def row(self, table, **match) -> dict | None:
        for row in self.rows(table):
            if all(str(row.get(k)) == str(v) for k, v in match.items()):
                return row
        return None

    def write_payloads(self, table, verb=None) -> list[dict]:
        return [payload for v, t, payload in self.writes
                if t == table and (verb is None or v == verb)]


def membership(user_id="u-1", school_id="sc-home", status="active", **extra) -> dict:
    return dict({"id": f"m-{user_id}-{school_id}", "user_id": user_id,
                 "school_id": school_id, "school_role": "guru", "status": status,
                 "joined_at": "2026-08-01T00:00:00+00:00"}, **extra)


def school(school_id, name="SMP Negeri 1", npsn="12345678", city="Bandung",
           status="active") -> dict:
    return {"id": school_id, "name": name, "npsn": npsn, "city": city,
            "status": status}


def person(user_id="u-1", name="Budi Guru", email="budi@example.test",
           status="active") -> dict:
    return {"id": user_id, "full_name": name, "email": email, "status": status}


def ok(data) -> SimpleNamespace:  # pragma: no cover - convenience for ad hoc use
    return SimpleNamespace(data=data)
