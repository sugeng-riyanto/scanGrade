"""Contract tests for school licensing (offline, no Supabase credentials)."""
from datetime import datetime, timezone
from app.services import live_school_entitlement as entitlement

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


class Query:
    def __init__(self, rows):
        self.rows = rows
    def select(self, *_): return self
    def eq(self, *_): return self
    def limit(self, *_): return self
    def execute(self):
        class Result: pass
        result = Result()
        result.data = self.rows
        return result


class DB:
    def __init__(self, rows=None, broken=False):
        self.rows, self.broken = rows, broken
    def table(self, name):
        assert name == "scanmeet_school_entitlements"
        if self.broken:
            raise RuntimeError("database unavailable")
        return Query(self.rows)


def test_missing_row_and_transport_failure_are_off():
    assert not entitlement.active(DB(), "school-1", now=NOW)
    assert not entitlement.active(DB(broken=True), "school-1", now=NOW)


def test_expired_disabled_and_unpaid_school_are_off():
    for status, enabled, expiry in (
        ("paid", True, "2026-10-24T00:00:00Z"),
        ("disabled", False, "2026-11-24T00:00:00Z"),
        ("expired", True, "2026-11-24T00:00:00Z"),
        ("paid", True, None),
        ("paid", True, "not-a-date"),
        ("paid", True, "2026-11-24T00:00:00"),
    ):
        assert not entitlement.active(DB([dict(status=status, enabled=enabled,
                                               valid_until=expiry)]), "school-1", now=NOW)


def test_only_live_entitled_school_can_join():
    for status in ("trial", "paid"):
        assert entitlement.active(DB([dict(status=status, enabled=True,
            valid_until="2026-11-24T00:00:00Z")]), "school-1", now=NOW)


def test_server_media_boundaries_enforce_school_entitlement():
    from pathlib import Path
    route = (Path(__file__).resolve().parents[2] / "app/routes/live.py").read_text()
    assert route.count("live_school_entitlement.active(") >= 3
    assert 'session["school_id"]' in route
    assert 'exam["school_id"]' in route
