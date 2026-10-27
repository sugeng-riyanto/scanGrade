"""Offline security regression tests for the ScanMeet entitlement decision.

These tests cover NEW admission only. They do NOT prove existing LiveKit
participants are disconnected when an entitlement is revoked; that requires
a separate room-revocation implementation and real-browser/SFU verification.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import live_school_entitlement

NOW = datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc)


class StubQuery:
    def __init__(self, rows):
        self.rows = rows
        self.school_filter = None

    def select(self, columns):
        assert columns == "enabled,status,valid_until"
        return self

    def eq(self, key, value):
        assert key == "school_id"
        self.school_filter = value
        return self

    def limit(self, count):
        assert count == 1
        return self

    def execute(self):
        assert self.school_filter == "school-a"
        return type("Result", (), {"data": self.rows})()


class StubDB:
    def __init__(self, rows=None, *, error=None):
        self.rows, self.error = rows, error
        self.calls = 0

    def table(self, name):
        self.calls += 1
        assert name == "scanmeet_school_entitlements"
        if self.error:
            raise self.error
        return StubQuery(self.rows)


@pytest.mark.parametrize("row", [
    None,
    {},
    {"enabled": False, "status": "paid", "valid_until": "2099-01-01T00:00:00Z"},
    {"enabled": 1, "status": "paid", "valid_until": "2099-01-01T00:00:00Z"},
    {"enabled": True, "status": "disabled", "valid_until": "2099-01-01T00:00:00Z"},
    {"enabled": True, "status": "expired", "valid_until": "2099-01-01T00:00:00Z"},
    {"enabled": True, "status": "paid", "valid_until": None},
    {"enabled": True, "status": "paid", "valid_until": ""},
    {"enabled": True, "status": "paid", "valid_until": "not-a-date"},
    {"enabled": True, "status": "paid", "valid_until": "2026-10-26T02:00:00Z"},
    {"enabled": True, "status": "trial", "valid_until": "2026-10-26T01:59:59Z"},
    {"enabled": True, "status": "paid", "valid_until": "2026-10-26T03:00:00"},
])
def test_entitlement_denies_absent_invalid_or_expired_rows(row):
    rows = [] if row is None else [row]
    assert live_school_entitlement.active(StubDB(rows), "school-a", now=NOW) is False


@pytest.mark.parametrize("status", ["trial", "paid"])
def test_future_valid_paid_or_trial_entitlement_allows_new_admission(status):
    row = {"enabled": True, "status": status,
           "valid_until": (NOW + timedelta(seconds=1)).isoformat()}
    assert live_school_entitlement.active(StubDB([row]), "school-a", now=NOW) is True


def test_missing_school_id_never_queries_database():
    db = StubDB(error=RuntimeError("must not be called"))
    assert live_school_entitlement.active(db, "", now=NOW) is False
    assert db.calls == 0


def test_database_exception_fails_closed():
    db = StubDB(error=ConnectionError("db not reachable"))
    assert live_school_entitlement.active(db, "school-a", now=NOW) is False


def test_offset_aware_expiry_is_compared_to_utc():
    row = {"enabled": True, "status": "paid",
           "valid_until": "2026-10-26T09:00:01+07:00"}
    assert live_school_entitlement.active(StubDB([row]), "school-a", now=NOW)
    row["valid_until"] = "2026-10-26T09:00:00+07:00"
    assert not live_school_entitlement.active(StubDB([row]), "school-a", now=NOW)
