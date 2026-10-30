"""Offline rows for request/admin behavior. SQL reservations are tested separately."""
import copy
from fnmatch import fnmatchcase
from types import SimpleNamespace
import uuid


class MemoryDB:
    def __init__(self, **tables):
        self.rows = copy.deepcopy(tables)
        self.calls = []
        self.before_update = None

    def table(self, name):
        self.calls.append(name)
        return Query(self, name)


class Query:
    def __init__(self, db, table):
        self.db, self.name = db, table
        self.filters, self.take, self.start = [], None, 0
        self.operation, self.payload = "select", None
        self.order_by, self.desc = None, False

    def select(self, *args, **kwargs): return self
    def eq(self, key, value):
        self.filters.append(lambda r: str(r.get(key)) == str(value))
        return self
    def in_(self, key, values):
        self.filters.append(lambda r: r.get(key) in values)
        return self
    def ilike(self, key, value):
        self.filters.append(lambda r: fnmatchcase(str(r.get(key, "")).casefold(), value.replace("%", "*").casefold()))
        return self
    def order(self, key, desc=False):
        self.order_by, self.desc = key, desc
        return self
    def limit(self, value):
        self.take = value
        return self
    def range(self, start, end):
        self.start, self.take = start, end - start + 1
        return self
    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self
    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self
    def execute(self):
        source = self.db.rows.setdefault(self.name, [])
        if self.operation == "insert":
            row = dict(self.payload)
            if self.name == "scanmeet_plan_requests":
                if any(r["school_id"] == row["school_id"] and r.get("status") == "pending" for r in source):
                    raise RuntimeError("unique pending request")
                row.setdefault("id", str(uuid.uuid4()))
            source.append(row)
            return SimpleNamespace(data=[copy.deepcopy(row)], count=1)
        if self.operation == "update" and self.db.before_update:
            self.db.before_update(self.name, source)
        rows = [r for r in source if all(test(r) for test in self.filters)]
        count = len(rows)
        if self.order_by:
            rows.sort(key=lambda r: str(r.get(self.order_by) or ""), reverse=self.desc)
        rows = rows[self.start:self.start + self.take] if self.take is not None else rows[self.start:]
        if self.operation == "update":
            for row in rows:
                row.update(self.payload)
        return SimpleNamespace(data=copy.deepcopy(rows), count=count)


SCHOOL = {"id": "school-a", "npsn": "69893227", "name": "SMA Harapan Bangsa — DEMO"}
