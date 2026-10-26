"""Offline expiry sweep tests: no sockets, DB credentials, or pupil cameras."""
from datetime import datetime, timedelta, timezone

from app.services.live_expiry_cleanup import sweep_expired

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(minutes=1)).isoformat()
FUTURE = (NOW + timedelta(days=1)).isoformat()


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = []
        self.patch = None

    def select(self, columns): return self
    def eq(self, field, value):
        self.filters.append((field, "eq", value)); return self
    def in_(self, field, values):
        self.filters.append((field, "in", values)); return self
    def lte(self, field, value):
        self.filters.append((field, "lte", value)); return self
    def limit(self, value): self.max_rows = value; return self
    def update(self, values):
        self.patch = values; return self

    def execute(self):
        class Response:
            def __init__(self, data): self.data = data
        rows = self.db.data[self.table]
        for field, op, value in self.filters:
            if op == "eq": rows = [r for r in rows if r.get(field) == value]
            elif op == "in": rows = [r for r in rows if r.get(field) in value]
            else: rows = [r for r in rows if r.get(field) is not None and r[field] <= value]
        rows = rows[:getattr(self, "max_rows", len(rows))]
        if self.patch is not None:
            for row in rows: row.update(self.patch)
        return Response([dict(r) for r in rows])


class Database:
    def __init__(self, valid_until=OLD):
        self.data = {
            "scanmeet_school_entitlements": [
                {"school_id": "school-a", "enabled": True,
                 "status": "trial", "valid_until": valid_until}
            ],
            "scan_live_sessions": [
                {"id": "session-a", "school_id": "school-a",
                 "room_name": "room-a", "status": "active"},
                {"id": "session-b", "school_id": "school-b",
                 "room_name": "room-b", "status": "active"},
            ],
        }

    def table(self, name): return Query(self, name)


def test_expired_license_ends_only_its_own_school_room():
    db = Database()
    deleted = []
    result = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW)
    assert result["measured"] is True
    assert result["ended"] == 1
    assert result["cleanup_pending"] == []
    assert deleted == ["room-a"]
    assert db.data["scan_live_sessions"][0]["status"] == "ended"
    assert db.data["scan_live_sessions"][1]["status"] == "active"


def test_unexpired_license_does_not_disconnect():
    db = Database(FUTURE)
    deleted = []
    result = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW)
    assert result["ended"] == 0
    assert deleted == []


def test_delete_failure_is_durable_debt_not_a_success_claim():
    db = Database()
    queued = []
    def fail_delete(room): raise ConnectionError("LiveKit unavailable")
    def enqueue(*args, **kwargs):
        queued.append(kwargs["reason"])
        return {"id": "debt-a"}
    result = sweep_expired(db, end_room=fail_delete, enqueue=enqueue, now=NOW)
    assert result["cleanup_pending"] == ["session-a"]
    assert result["cleanup_unrecorded"] == []
    assert queued == ["school_license_expired"]


def test_queue_failure_is_reported_as_unrecorded():
    db = Database()
    def fail_delete(room): raise ConnectionError("LiveKit unavailable")
    def fail_enqueue(*args, **kwargs): raise RuntimeError("DB unavailable")
    result = sweep_expired(db, end_room=fail_delete,
                           enqueue=fail_enqueue, now=NOW)
    assert result["cleanup_pending"] == ["session-a"]
    assert result["cleanup_unrecorded"] == ["session-a"]
