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
    def order(self, field): self.order_field = field; return self
    def range(self, start, end): self.offset = start; self.max_rows = end - start + 1; return self
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
        if hasattr(self, "order_field"):
            rows = sorted(rows, key=lambda row: row[self.order_field])
        offset = getattr(self, "offset", 0)
        rows = rows[offset:offset + getattr(self, "max_rows", len(rows))]
        if self.patch is not None:
            for row in rows: row.update(self.patch)
        return Response([dict(r) for r in rows])


class Database:
    def __init__(self, valid_until=OLD):
        self.data = {
            "scanmeet_school_entitlements": [
                {"school_id": "school-a", "enabled": True,
                 "status": "trial", "valid_until": valid_until},
                # school-b is licensed on purpose: "a lapsed school's sweep does
                # not touch another school's room" is only a claim about isolation
                # while that other school is entitled. Session enumeration means a
                # school with no row would now be revoked on its own merits.
                {"school_id": "school-b", "enabled": True,
                 "status": "paid", "valid_until": FUTURE},
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


def test_pagination_reaches_expired_schools_beyond_first_page():
    db = Database()
    db.data["scanmeet_school_entitlements"] = [
        {"school_id": "school-%03d" % i, "enabled": True,
         "status": "trial", "valid_until": OLD}
        for i in range(30)
    ]
    db.data["scan_live_sessions"] = [
        {"id": "session-29", "school_id": "school-029",
         "room_name": "last-room", "status": "active"}
    ]
    deleted = []
    result = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW, limit=25)
    assert result["schools"] == 30
    assert result["ended"] == 1
    assert deleted == ["last-room"]


def test_more_than_one_hundred_active_rooms_are_terminated():
    db = Database()
    db.data["scan_live_sessions"] = [
        {"id": "session-%03d" % i, "school_id": "school-a",
         "room_name": "room-%03d" % i, "status": "active"}
        for i in range(121)
    ]
    deleted = []
    result = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW)
    assert result["ended"] == 121
    assert len(deleted) == 121
    assert all(row["status"] == "ended" for row in db.data["scan_live_sessions"])


def test_renewal_during_sweep_stops_further_disconnects():
    db = Database()
    db.data["scan_live_sessions"] = [
        {"id": "session-first", "school_id": "school-a",
         "room_name": "room-first", "status": "active"},
        {"id": "session-second", "school_id": "school-a",
         "room_name": "room-second", "status": "active"},
    ]
    deleted = []
    def delete_and_renew(room):
        deleted.append(room)
        db.data["scanmeet_school_entitlements"][0]["valid_until"] = FUTURE
    result = sweep_expired(db, end_room=delete_and_renew,
                           enqueue=lambda *a, **k: None, now=NOW)
    assert result["ended"] == 1
    assert deleted == ["room-first"]
    assert db.data["scan_live_sessions"][1]["status"] == "active"


def test_entitlement_read_error_never_authorizes_room_deletion():
    db = Database()
    original_table = db.table
    calls = {"entitlement": 0}
    def table(name):
        query = original_table(name)
        if name == "scanmeet_school_entitlements":
            original_execute = query.execute
            def execute():
                calls["entitlement"] += 1
                if calls["entitlement"] >= 2:
                    raise ConnectionError("temporary entitlement DB outage")
                return original_execute()
            query.execute = execute
        return query
    db.table = table
    deleted = []
    report = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW)
    assert deleted == []
    assert report["ended"] == 0
    assert report["errors"]
    assert db.data["scan_live_sessions"][0]["status"] == "active"


def test_entitlement_read_failure_mid_batch_stops_further_deletion():
    """A licence that becomes unreadable mid-pass deletes nothing further.

    The failure is triggered by the first successful deletion rather than by
    counting queries: a count encodes how many reads *this* implementation
    happens to make, and the guarantee does not depend on that number.
    """
    db = Database()
    db.data["scan_live_sessions"] = [
        {"id": "first", "school_id": "school-a",
         "room_name": "room-first", "status": "active"},
        {"id": "second", "school_id": "school-a",
         "room_name": "room-second", "status": "active"},
    ]
    deleted = []
    offline = {"on": False}

    def delete_first(room):
        deleted.append(room)
        offline["on"] = True          # the licence database goes away after this

    original_table = db.table

    def table(name):
        query = original_table(name)
        if name == "scanmeet_school_entitlements":
            execute = query.execute

            def guarded_execute():
                if offline["on"]:
                    raise ConnectionError("license DB offline")
                return execute()
            query.execute = guarded_execute
        return query

    db.table = table
    result = sweep_expired(db, end_room=delete_first,
                           enqueue=lambda *a, **k: None, now=NOW)
    assert deleted == ["room-first"]
    assert db.data["scan_live_sessions"][1]["status"] == "active"
    assert result["errors"]

# ── a licence switched OFF, not merely expired ──────────────────────────────
def _disable(db):
    """Exactly what the super-admin form stores when a licence is switched off."""
    db.data["scanmeet_school_entitlements"][0].update(
        {"enabled": False, "status": "disabled", "valid_until": None})


def test_a_disabled_licence_ends_the_room_that_is_already_running():
    """The P0 row an expiry-only sweep could never reach.

    `enabled=false` forces `status='disabled'` and `valid_until=NULL`, so a sweep
    that asks for enabled rows past their deadline never saw this school at all —
    the room kept publishing pupil video after the add-on was switched off.
    """
    db = Database()
    _disable(db)
    deleted = []

    report = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW)

    assert deleted == ["room-a"]
    assert report["ended"] == 1
    assert report["revoked_schools"] == 1 and report["expired_schools"] == 0
    assert db.data["scan_live_sessions"][0]["status"] == "ended"
    # Isolation still holds: the licensed school beside it is untouched.
    assert db.data["scan_live_sessions"][1]["status"] == "active"


def test_a_disabled_licence_records_its_own_reason_in_the_ledger():
    db = Database()
    _disable(db)
    reasons = []

    def record(_db, session, reason=None, error=None):
        reasons.append(reason)
        return {"id": "job"}

    def unreachable(room):
        raise RuntimeError("LiveKit unreachable")

    report = sweep_expired(db, end_room=unreachable, enqueue=record, now=NOW)

    assert reasons == ["school_license_disabled"]
    assert report["cleanup_pending"] == ["session-a"]
    assert report["cleanup_unrecorded"] == []
    assert report["errors"] == []


def test_a_status_that_is_not_trial_or_paid_revokes_even_when_enabled():
    """A contradiction in the row is still a no: `enabled` cannot outvote `status`."""
    db = Database(valid_until=FUTURE)
    db.data["scanmeet_school_entitlements"][0]["status"] = "expired"
    deleted = []

    report = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW)

    assert deleted == ["room-a"] and report["revoked_schools"] == 1


def test_a_deleted_licence_row_still_ends_the_room_it_left_running():
    """No row is a definitive no — and the session is how the school is reached.

    Enumerating licences alone would miss this: with the row gone there is nothing
    to list the school by, so the sweep takes its key set from active sessions too.
    """
    db = Database()
    db.data["scanmeet_school_entitlements"] = []
    deleted = []

    report = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW)

    assert sorted(deleted) == ["room-a", "room-b"]
    assert report["revoked_schools"] == 2 and report["expired_schools"] == 0
    assert report["errors"] == []


def test_a_school_with_two_licence_rows_is_ambiguous_and_keeps_its_room():
    db = Database()
    db.data["scanmeet_school_entitlements"].append(
        {"school_id": "school-a", "enabled": False, "status": "disabled",
         "valid_until": None})
    deleted = []

    report = sweep_expired(db, end_room=deleted.append,
                           enqueue=lambda *a, **k: None, now=NOW)

    assert deleted == []
    assert report["errors"]
    assert db.data["scan_live_sessions"][0]["status"] == "active"
